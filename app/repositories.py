from hashlib import sha256

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import ShortLink, User, WatchStock


def get_or_create_user(session: Session, conversation_id: str) -> User:
    # The existing column name is retained for backward compatibility. It now
    # stores a LINE user ID for private chats, group ID for groups, or room ID.
    user = session.scalar(select(User).where(User.line_user_id == conversation_id))
    if user:
        return user
    user = User(line_user_id=conversation_id)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def list_codes(session: Session, user: User) -> list[str]:
    return list(
        session.scalars(
            select(WatchStock.stock_code)
            .where(WatchStock.user_id == user.id)
            .order_by(WatchStock.stock_code)
        )
    )


def add_codes(session: Session, user: User, codes: list[str]) -> tuple[list[str], list[str]]:
    existing = set(list_codes(session, user))
    added = [code for code in codes if code not in existing]
    skipped = [code for code in codes if code in existing]
    session.add_all(WatchStock(user_id=user.id, stock_code=code) for code in added)
    session.commit()
    return added, skipped


def remove_codes(session: Session, user: User, codes: list[str]) -> list[str]:
    existing = set(list_codes(session, user))
    removed = [code for code in codes if code in existing]
    if removed:
        session.execute(
            delete(WatchStock).where(
                WatchStock.user_id == user.id, WatchStock.stock_code.in_(removed)
            )
        )
        session.commit()
    return removed


def save_short_links(session: Session, urls: list[str]) -> dict[str, str]:
    links = {url: sha256(url.encode()).hexdigest()[:16] for url in dict.fromkeys(urls)}
    if not links:
        return links
    existing = {
        row.slug: row.url
        for row in session.scalars(
            select(ShortLink).where(ShortLink.slug.in_(links.values()))
        )
    }
    for url, slug in links.items():
        if slug in existing and existing[slug] != url:
            raise RuntimeError("短網址雜湊衝突")
        if slug not in existing:
            session.add(ShortLink(slug=slug, url=url))
    session.commit()
    return links

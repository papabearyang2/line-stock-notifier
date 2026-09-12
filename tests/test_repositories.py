from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ShortLink
from app.repositories import save_short_links


def test_save_short_links_reuses_deterministic_slug():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    url = "https://example.com/news/a-very-long-article-url"

    with Session(engine) as session:
        first = save_short_links(session, [url])
        second = save_short_links(session, [url])
        stored = session.get(ShortLink, first[url])

    assert first == second
    assert stored and stored.url == url

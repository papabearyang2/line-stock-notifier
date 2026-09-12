from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings


async def publish_chart(path: Path) -> str:
    """Publish a chart and return the HTTPS URL LINE should download.

    Local/Docker mode serves the file through FastAPI. Vercel mode uploads to
    a public Vercel Blob store because a function's temporary filesystem is not
    durable and a later image request may reach another instance.
    """

    settings = get_settings()
    if settings.blob_read_write_token:
        from vercel.blob import AsyncBlobClient

        client = AsyncBlobClient(token=settings.blob_read_write_token)
        uploaded = await client.put(
            f"charts/{path.name}",
            path.read_bytes(),
            access="public",
            content_type="image/png",
            add_random_suffix=False,
        )
        path.unlink(missing_ok=True)
        return uploaded.url

    if settings.is_vercel:
        path.unlink(missing_ok=True)
        raise RuntimeError("Vercel 部署尚未連接 Public Blob 儲存空間")
    return f"{settings.public_base_url}/artifacts/{path.name}"


async def cleanup_published_charts(retention_days: int = 7) -> int:
    settings = get_settings()
    if not settings.blob_read_write_token:
        return 0
    from vercel.blob import AsyncBlobClient

    client = AsyncBlobClient(token=settings.blob_read_write_token)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    cursor: str | None = None
    stale_urls: list[str] = []
    while True:
        page = await client.list_objects(prefix="charts/", limit=1000, cursor=cursor)
        stale_urls.extend(
            item.url
            for item in page.blobs
            if item.uploaded_at.astimezone(timezone.utc) < cutoff
        )
        if not page.has_more or not page.cursor:
            break
        cursor = page.cursor
    if stale_urls:
        await client.delete(stale_urls)
    return len(stale_urls)

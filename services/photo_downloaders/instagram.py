from pathlib import Path
from typing import Any

import httpx
import yt_dlp

from config import BROWSER_HEADERS


def _collect_instagram_image_urls(
    info: dict[str, Any],
) -> list[str]:
    """
    Собирает ссылки на изображения из данных yt-dlp.
    Поддерживает одиночные посты и карусели.
    """
    found: list[str] = []

    def add_url(value: Any) -> None:
        if not isinstance(value, str):
            return

        lowered = value.lower()

        if not value.startswith("http"):
            return

        if any(
            marker in lowered
            for marker in (
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
                "fbcdn.net",
                "cdninstagram.com",
            )
        ):
            found.append(value)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            ext = str(value.get("ext") or "").lower()
            media_url = value.get("url")

            if ext in {
                "jpg",
                "jpeg",
                "png",
                "webp",
            }:
                add_url(media_url)

            thumbnails = value.get("thumbnails")

            if isinstance(thumbnails, list):
                for thumbnail in thumbnails:
                    if isinstance(thumbnail, dict):
                        add_url(thumbnail.get("url"))

            add_url(value.get("thumbnail"))

            entries = value.get("entries")

            if isinstance(entries, list):
                for entry in entries:
                    walk(entry)

        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(info)

    return list(dict.fromkeys(found))


def download_instagram_photos(
    url: str,
    folder: str,
) -> list[Path]:
    """
    Загружает фотографии из публичного Instagram-поста
    или карусели.
    """
    options: dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "socket_timeout": 60,
        "retries": 2,
    }

    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(
                url,
                download=False,
            )

    except yt_dlp.utils.DownloadError as error:
        raise RuntimeError(
            f"Instagram не отдал данные публикации: {error}"
        ) from error

    if not isinstance(info, dict):
        raise RuntimeError(
            "Instagram не вернул данные публикации."
        )

    photo_urls = _collect_instagram_image_urls(
        info
    )

    if not photo_urls:
        raise RuntimeError(
            "Instagram не отдал список фотографий."
        )

    headers = {
        **BROWSER_HEADERS,
        "Referer": url,
    }

    downloaded: list[Path] = []

    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(60),
    ) as client:
        for index, photo_url in enumerate(
            photo_urls,
            start=1,
        ):
            try:
                response = client.get(photo_url)
                response.raise_for_status()

                content_type = response.headers.get(
                    "content-type",
                    "",
                ).lower()

                if not content_type.startswith("image/"):
                    continue

                if "png" in content_type:
                    extension = ".png"
                elif "webp" in content_type:
                    extension = ".webp"
                else:
                    extension = ".jpg"

                path = Path(folder) / (
                    "instagram_photo_"
                    f"{index:02d}"
                    f"{extension}"
                )

                path.write_bytes(
                    response.content
                )

                if path.stat().st_size < 5_000:
                    path.unlink(
                        missing_ok=True
                    )
                    continue

                downloaded.append(path)

            except httpx.HTTPError:
                continue

    if not downloaded:
        raise RuntimeError(
            "Instagram не разрешил скачать фотографии."
        )

    return downloaded

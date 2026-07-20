from pathlib import Path

from services.photo_downloaders.instagram import (
    download_instagram_photos,
)
from services.photo_downloaders.tiktok import (
    download_tiktok_photos,
)


def download_photos(
    url: str,
    folder: str,
) -> list[Path]:
    lowered = url.lower()

    if "instagram.com" in lowered:
        return download_instagram_photos(
            url,
            folder,
        )

    if "tiktok.com" in lowered:
        return download_tiktok_photos(
            url,
            folder,
        )

    raise RuntimeError(
        "Эта платформа пока не поддерживает фотографии."
    )

from pathlib import Path

from services.photo_downloaders.instagram import (
    download_instagram_media,
)
from services.photo_downloaders.tiktok import (
    download_tiktok_photos,
)


def download_photos(
    url: str,
    folder: str,
) -> list[Path]:
    """
    Загружает фотографии или видео из поддерживаемых платформ.

    Возвращает список файлов (изображения и/или видео)
    в том порядке, в котором они были опубликованы.
    """
    lowered = url.lower()

    if "instagram.com" in lowered:
        return download_instagram_media(
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
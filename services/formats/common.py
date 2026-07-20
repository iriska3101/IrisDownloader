from typing import Any

from services.formats.instagram import get_instagram_options
from services.formats.tiktok import get_tiktok_options
from services.formats.youtube import get_youtube_options


def get_platform_options(
    url: str,
) -> tuple[str, dict[str, Any]]:
    """
    Определяет платформу по ссылке
    и возвращает соответствующие настройки.
    """
    url = url.lower()

    if "tiktok.com" in url:
        return "TIKTOK", get_tiktok_options()

    if "instagram.com" in url:
        return "INSTAGRAM", get_instagram_options()

    if (
        "youtube.com" in url
        or "youtu.be" in url
    ):
        return "YOUTUBE", get_youtube_options()

    return (
        "OTHER",
        {
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
        },
    )

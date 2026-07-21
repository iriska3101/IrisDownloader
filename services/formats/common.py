from pathlib import Path
from typing import Any

from services.formats.instagram import get_instagram_options
from services.formats.tiktok import get_tiktok_options
from services.formats.youtube import get_youtube_options


COOKIES_FILE = Path("/etc/secrets/cookies.txt")


def _add_cookies_if_available(
    options: dict[str, Any],
) -> dict[str, Any]:
    """
    Подключает cookies, если файл добавлен
    в Render как Secret File.
    """
    if (
        COOKIES_FILE.exists()
        and COOKIES_FILE.is_file()
        and COOKIES_FILE.stat().st_size > 0
    ):
        options["cookiefile"] = str(COOKIES_FILE)

        print(
            "IRISSAVE COOKIES: подключены",
            flush=True,
        )
    else:
        print(
            "IRISSAVE COOKIES: файл не найден",
            flush=True,
        )

    return options


def get_platform_options(
    url: str,
) -> tuple[str, dict[str, Any]]:
    """
    Определяет платформу по ссылке
    и возвращает соответствующие настройки.
    """
    url = url.lower()

    if "tiktok.com" in url:
        return (
            "TIKTOK",
            get_tiktok_options(),
        )

    if "instagram.com" in url:
        return (
            "INSTAGRAM",
            _add_cookies_if_available(
                get_instagram_options()
            ),
        )

    if (
        "youtube.com" in url
        or "youtu.be" in url
    ):
        return (
            "YOUTUBE",
            _add_cookies_if_available(
                get_youtube_options()
            ),
        )

    return (
        "OTHER",
        {
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
        },
    )
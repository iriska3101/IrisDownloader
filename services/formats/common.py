import os
from pathlib import Path
from typing import Any

from services.formats.instagram import get_instagram_options
from services.formats.tiktok import get_tiktok_options
from services.formats.youtube import get_youtube_options


COOKIES_FILE = Path("/etc/secrets/cookies.txt")
YOUTUBE_COOKIES_ENV_FILE = Path("/tmp/irissave-youtube-cookies.txt")


def _materialize_youtube_cookies_from_env() -> Path | None:
    """
    Позволяет передать Netscape cookies через Render Environment Variable.

    Это запасной вариант для телефона, когда Secret File создать неудобно.
    Значение никогда не печатается в логах.
    """
    raw = os.getenv("IRISSAVE_YOUTUBE_COOKIES", "")
    if not raw.strip():
        return None

    try:
        normalized = raw.replace("\\n", "\n").strip() + "\n"
        YOUTUBE_COOKIES_ENV_FILE.write_text(normalized, encoding="utf-8")
        os.chmod(YOUTUBE_COOKIES_ENV_FILE, 0o600)
        return YOUTUBE_COOKIES_ENV_FILE
    except OSError as error:
        print(
            "IRISSAVE COOKIES: не удалось подготовить YouTube cookies из Environment | "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
        return None


def _add_cookies_if_available(
    options: dict[str, Any],
    *,
    platform: str,
) -> dict[str, Any]:
    """Подключает cookies, если они доступны."""
    cookie_path: Path | None = None

    if (
        COOKIES_FILE.exists()
        and COOKIES_FILE.is_file()
        and COOKIES_FILE.stat().st_size > 0
    ):
        cookie_path = COOKIES_FILE

    elif platform == "YOUTUBE":
        cookie_path = _materialize_youtube_cookies_from_env()

    if cookie_path is not None:
        options["cookiefile"] = str(cookie_path)
        print(
            f"IRISSAVE COOKIES: подключены для {platform}",
            flush=True,
        )
    else:
        print(
            f"IRISSAVE COOKIES: не настроены для {platform}",
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
            _add_cookies_if_available(
                get_tiktok_options(),
                platform="TIKTOK",
            ),
        )

    if "instagram.com" in url:
        return (
            "INSTAGRAM",
            _add_cookies_if_available(
                get_instagram_options(),
                platform="INSTAGRAM",
            ),
        )

    if (
        "youtube.com" in url
        or "youtu.be" in url
    ):
        return (
            "YOUTUBE",
            _add_cookies_if_available(
                get_youtube_options(),
                platform="YOUTUBE",
            ),
        )

    return (
        "OTHER",
        {
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
        },
    )

from typing import Any

import yt_dlp

from utils.helpers import clean_text


def search_music_results(
    query: str,
) -> list[dict[str, str]]:
    """Ищет на YouTube пять вариантов по названию песни."""
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 45,
        "retries": 2,
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(
            f"ytsearch5:{query}",
            download=False,
        )

    entries = info.get("entries") or []
    results: list[dict[str, str]] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        webpage_url = (
            entry.get("webpage_url")
            or entry.get("url")
        )

        if not isinstance(webpage_url, str):
            continue

        if not webpage_url.startswith("http"):
            webpage_url = (
                "https://www.youtube.com/watch?v="
                f"{webpage_url}"
            )

        title = clean_text(
            entry.get("title"),
            "Без названия",
            max_length=70,
        )

        uploader = clean_text(
            entry.get("uploader")
            or entry.get("channel"),
            "",
            max_length=45,
        )

        results.append(
            {
                "title": title,
                "uploader": uploader,
                "url": webpage_url,
            }
        )

    return results
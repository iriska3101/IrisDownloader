from typing import Any


def get_tiktok_options() -> dict[str, Any]:
    """
    Настройки TikTok.

    Используем browser impersonation, потому что
    TikTok часто блокирует обычные HTTP-запросы yt-dlp.
    """
    return {
        "format": (
            "best[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "[acodec!=none]/"
            "best[ext=mp4]"
            "[acodec!=none]/"
            "best[acodec!=none]"
        ),
        "format_sort": [
            "vcodec:h264",
            "res",
            "fps",
            "hasaud",
        ],
        "merge_output_format": "mp4",
        "impersonate": "chrome",
    }
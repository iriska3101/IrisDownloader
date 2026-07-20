from typing import Any


def get_tiktok_options() -> dict[str, Any]:
    """
    Проверенные настройки TikTok.
    Видео скачивается со звуком.
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
    }

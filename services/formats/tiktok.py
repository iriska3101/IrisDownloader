from typing import Any


def get_tiktok_options() -> dict[str, Any]:
    """
    Настройки загрузки TikTok.

    Не форсируем browser impersonation:
    доступность конкретных targets зависит
    от request handlers и curl_cffi в окружении Render.

    TikTok direct fallback обрабатывается отдельно
    в services/video_progress.py.
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
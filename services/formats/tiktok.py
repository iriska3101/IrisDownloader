from typing import Any


def get_tiktok_options() -> dict[str, Any]:
    """
    Настройки загрузки TikTok.

    На Render у проекта уже установлен yt-dlp с curl-cffi,
    поэтому для TikTok принудительно включаем browser
    impersonation. Это даёт yt-dlp возможность выполнять
    запросы к TikTok через curl_cffi как браузер, а не через
    обычный urllib/http-клиент.

    TikTok direct fallback по-прежнему обрабатывается отдельно
    в services/video_progress.py.
    """
    return {
        "impersonate": "chrome",
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

from typing import Any

from yt_dlp.networking.impersonate import ImpersonateTarget


def get_tiktok_options() -> dict[str, Any]:
    """
    Настройки загрузки TikTok.

    При использовании yt-dlp как Python API параметр ``impersonate``
    должен быть уже разобран в ImpersonateTarget. CLI сам делает это
    преобразование, но YoutubeDL(options) со строкой ``"chrome"`` его
    не выполняет, из-за чего возникал AssertionError.

    TikTok direct fallback по-прежнему обрабатывается отдельно
    в services/video_progress.py.
    """
    return {
        "impersonate": ImpersonateTarget.from_str("chrome"),
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

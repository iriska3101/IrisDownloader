from typing import Any


def get_instagram_options() -> dict[str, Any]:
    """
    Настройки для Instagram Reels и видеопостов.

    Сначала берём готовый MP4-файл Instagram со звуком.
    Это сохраняет родные пропорции/метаданные ролика и
    не заставляет yt-dlp склеивать отдельные DASH-потоки,
    которые у некоторых Reels дают неверное отображение
    пропорций в Telegram.

    Если готового MP4 нет, используем прежние fallback-варианты.
    Stories пока не поддерживаются.
    """
    return {
        "format": (
            "b[ext=mp4][acodec!=none]/"
            "bv*[ext=mp4]+ba[ext=m4a]/"
            "b[ext=mp4]/"
            "bv*+ba/b"
        ),
        "format_sort": [
            "res",
            "fps",
            "hasaud",
        ],
        "merge_output_format": "mp4",
    }

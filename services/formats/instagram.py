from typing import Any


def get_instagram_options() -> dict[str, Any]:
    """
    Настройки для Instagram Reels и видеопостов.
    Stories пока не поддерживаются.
    """
    return {
        "format": (
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

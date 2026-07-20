from typing import Any


def get_youtube_options() -> dict[str, Any]:
    """
    Настройки для YouTube и YouTube Shorts.
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

import os
from pathlib import Path
from typing import Any, Callable

import yt_dlp


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[[dict[str, Any]], None],
) -> Path:
    """Скачивает видео и передаёт реальный прогресс загрузки."""
    template = os.path.join(
        folder,
        "%(title).80s-%(id)s.%(ext)s",
    )

    options: dict[str, Any] = {
        "outtmpl": template,
        "format": "best[ext=mp4]/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "socket_timeout": 45,
        "retries": 2,
        "progress_hooks": [progress_hook],
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(
            url,
            download=True,
        )

        downloaded_path = downloader.prepare_filename(
            info
        )

    path = Path(downloaded_path)

    if not path.exists():
        files = [
            file
            for file in Path(folder).iterdir()
            if file.is_file()
        ]

        if files:
            path = max(
                files,
                key=lambda item: item.stat().st_size,
            )

    if not path.exists():
        raise FileNotFoundError(
            "Скачанный видеофайл не найден"
        )

    return path
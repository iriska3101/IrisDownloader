import os
from pathlib import Path
from typing import Any, Callable

import imageio_ffmpeg
import yt_dlp


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[[dict[str, Any]], None],
) -> Path:
    """
    Скачивает видео и звук отдельно, если это нужно,
    а затем быстро объединяет их без перекодирования.
    """
    folder_path = Path(folder)
    folder_path.mkdir(parents=True, exist_ok=True)

    template = os.path.join(
        folder,
        "%(title).80s-%(id)s.%(ext)s",
    )

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    files_before = {
        file.resolve()
        for file in folder_path.iterdir()
        if file.is_file()
    }

    options: dict[str, Any] = {
        "outtmpl": template,

        "format": (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo+bestaudio/"
            "best[ext=mp4][vcodec!=none][acodec!=none]/"
            "best[vcodec!=none][acodec!=none]"
        ),

        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg_path,

        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,

        "socket_timeout": 120,
        "retries": 5,
        "fragment_retries": 5,

        "progress_hooks": [progress_hook],
    }

    print(
        f"Starting video download: {url}",
        flush=True,
    )

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(
            url,
            download=True,
        )

        prepared_path = Path(
            downloader.prepare_filename(info)
        )

    mp4_path = prepared_path.with_suffix(".mp4")

    if mp4_path.exists():
        downloaded_path = mp4_path
    elif prepared_path.exists():
        downloaded_path = prepared_path
    else:
        new_files = [
            file
            for file in folder_path.iterdir()
            if (
                file.is_file()
                and file.resolve() not in files_before
                and file.suffix.lower()
                in {".mp4", ".mov", ".mkv", ".webm"}
            )
        ]

        if not new_files:
            raise FileNotFoundError(
                "Скачанный видеофайл не найден"
            )

        downloaded_path = max(
            new_files,
            key=lambda item: item.stat().st_mtime,
        )

    print(
        f"Video ready: {downloaded_path} "
        f"({downloaded_path.stat().st_size} bytes)",
        flush=True,
    )

    return downloaded_path
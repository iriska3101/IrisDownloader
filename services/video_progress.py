import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import imageio_ffmpeg
import yt_dlp


def _normalize_video(source_path: Path) -> Path:
    """
    Делает обычный MP4, который Telegram показывает без растяжения,
    и сохраняет в нём звук.
    """
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    fixed_path = source_path.with_name(
        f"{source_path.stem}_fixed.mp4"
    )

    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(fixed_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Не удалось подготовить видео для Telegram:\n"
            f"{result.stderr[-1500:]}"
        )

    if not fixed_path.exists():
        raise FileNotFoundError(
            "Исправленный видеофайл не найден"
        )

    if source_path.exists():
        source_path.unlink()

    return fixed_path


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[[dict[str, Any]], None],
) -> Path:
    """Скачивает видео со звуком и исправляет пропорции для Telegram."""

    template = os.path.join(
        folder,
        "%(title).80s-%(id)s.%(ext)s",
    )

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    options: dict[str, Any] = {
        "outtmpl": template,

        # Берём лучшее видео и звук.
        # Если они лежат отдельно, yt-dlp соединит их через FFmpeg.
        "format": (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo+bestaudio/"
            "best[ext=mp4]/best"
        ),

        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg_path,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,

        # Даём TikTok больше времени на ответ.
        "socket_timeout": 120,
        "retries": 5,
        "fragment_retries": 5,

        "progress_hooks": [progress_hook],
    }

    folder_path = Path(folder)
    files_before = {
        file.resolve()
        for file in folder_path.iterdir()
        if file.is_file()
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(
            url,
            download=True,
        )

        prepared_path = Path(
            downloader.prepare_filename(info)
        )

    possible_mp4_path = prepared_path.with_suffix(".mp4")

    if possible_mp4_path.exists():
        downloaded_path = possible_mp4_path
    elif prepared_path.exists():
        downloaded_path = prepared_path
    else:
        new_files = [
            file
            for file in folder_path.iterdir()
            if file.is_file()
            and file.resolve() not in files_before
        ]

        if not new_files:
            raise FileNotFoundError(
                "Скачанный видеофайл не найден"
            )

        downloaded_path = max(
            new_files,
            key=lambda item: item.stat().st_mtime,
        )

    return _normalize_video(downloaded_path)
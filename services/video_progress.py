import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import imageio_ffmpeg
import yt_dlp


TELEGRAM_SAFE_SIZE = 48 * 1024 * 1024


def _find_downloaded_file(
    folder_path: Path,
    prepared_path: Path,
    files_before: set[Path],
) -> Path:
    """Находит итоговый видеофайл после загрузки и объединения."""

    mp4_path = prepared_path.with_suffix(".mp4")

    if mp4_path.exists():
        return mp4_path

    if prepared_path.exists():
        return prepared_path

    candidates = [
        file
        for file in folder_path.iterdir()
        if (
            file.is_file()
            and file.resolve() not in files_before
            and file.suffix.lower()
            in {
                ".mp4",
                ".mov",
                ".mkv",
                ".webm",
            }
        )
    ]

    if not candidates:
        raise FileNotFoundError(
            "Скачанный видеофайл не найден"
        )

    return max(
        candidates,
        key=lambda item: item.stat().st_mtime,
    )


def _remux_to_clean_mp4(
    source_path: Path,
) -> Path:
    """
    Пересобирает видео в чистый MP4 без перекодирования.

    Исправляет файлы, которые Telegram показывает,
    но не может нормально воспроизвести или сохранить.
    """
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    clean_path = source_path.with_name(
        f"{source_path.stem}_clean.mp4"
    )

    command = [
        ffmpeg_path,
        "-y",

        "-i",
        str(source_path),

        # Первая видеодорожка и первая аудиодорожка.
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",

        # Не переносим подозрительные метаданные.
        "-map_metadata",
        "-1",

        # Не перекодируем — быстро и без нагрузки.
        "-c:v",
        "copy",
        "-c:a",
        "copy",

        # Убираем метаданные поворота.
        "-metadata:s:v:0",
        "rotate=0",

        # Позволяет Telegram запускать видео сразу.
        "-movflags",
        "+faststart",

        # Не оставляем лишние дорожки.
        "-sn",
        "-dn",

        str(clean_path),
    ]

    print(
        f"Cleaning MP4 container: {source_path.name}",
        flush=True,
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )

    if result.returncode != 0:
        print(
            result.stderr[-4000:],
            flush=True,
        )

        raise RuntimeError(
            "Не удалось подготовить видео для Telegram"
        )

    if (
        not clean_path.exists()
        or clean_path.stat().st_size == 0
    ):
        raise FileNotFoundError(
            "Подготовленный видеофайл не найден"
        )

    try:
        source_path.unlink()
    except OSError:
        pass

    print(
        f"Clean MP4 ready: {clean_path.name} | "
        f"{clean_path.stat().st_size} bytes",
        flush=True,
    )

    return clean_path


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[[dict[str, Any]], None],
) -> Path:
    """
    Скачивает совместимое с Telegram видео со звуком.

    После загрузки быстро пересобирает MP4-контейнер,
    не перекодируя изображение.
    """
    folder_path = Path(folder)
    folder_path.mkdir(
        parents=True,
        exist_ok=True,
    )

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

        # Приоритет:
        # 1. готовый MP4 H.264 со звуком;
        # 2. H.264-видео + аудио M4A;
        # 3. запасные варианты.
        "format": (
            "best[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "[acodec!=none]/"

            "bestvideo[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "+bestaudio[ext=m4a]/"

            "bestvideo[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "+bestaudio/"

            "best[ext=mp4]"
            "[vcodec!=none]"
            "[acodec!=none]/"

            "bestvideo+bestaudio/"
            "best"
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
        f"Downloading video: {url}",
        flush=True,
    )

    with yt_dlp.YoutubeDL(
        options
    ) as downloader:
        info = downloader.extract_info(
            url,
            download=True,
        )

        prepared_path = Path(
            downloader.prepare_filename(info)
        )

    downloaded_path = _find_downloaded_file(
        folder_path=folder_path,
        prepared_path=prepared_path,
        files_before=files_before,
    )

    print(
        f"Downloaded file: {downloaded_path.name} | "
        f"{downloaded_path.stat().st_size} bytes",
        flush=True,
    )

    clean_path = _remux_to_clean_mp4(
        downloaded_path
    )

    if clean_path.stat().st_size > TELEGRAM_SAFE_SIZE:
        raise RuntimeError(
            "Видео получилось больше 48 МБ. "
            "Этот ролик пока слишком большой для отправки."
        )

    return clean_path
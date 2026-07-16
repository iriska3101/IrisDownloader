import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import imageio_ffmpeg
import yt_dlp


TELEGRAM_SAFE_SIZE = 48 * 1024 * 1024


def _find_downloaded_video(
    folder_path: Path,
    prepared_path: Path,
    files_before: set[Path],
) -> Path:
    """Находит готовый файл после загрузки yt-dlp."""
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


def _make_iphone_compatible_mp4(
    source_path: Path,
) -> Path:
    """
    Подготавливает MP4 для Telegram и iPhone.

    Видео не перекодируется.
    Звук преобразуется в AAC.
    Исправляются временные метки и структура MP4.
    """
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    output_path = source_path.with_name(
        f"{source_path.stem}_iphone.mp4"
    )

    command = [
        ffmpeg_path,
        "-y",

        # Создаём недостающие временные метки.
        "-fflags",
        "+genpts",

        "-i",
        str(source_path),

        # Берём первую картинку и звук, если он есть.
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",

        # Не переносим подозрительные метаданные.
        "-map_metadata",
        "-1",

        # Картинку не пережимаем.
        "-c:v",
        "copy",

        # Звук обязательно делаем совместимым с iPhone.
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-ac",
        "2",

        # Исправляем отрицательные временные метки.
        "-avoid_negative_ts",
        "make_zero",

        # Убираем возможный неправильный поворот.
        "-metadata:s:v:0",
        "rotate=0",

        # Начало файла переносится вперёд:
        # Telegram и iPhone смогут нормально запускать ролик.
        "-movflags",
        "+faststart",

        # Убираем субтитры и другие лишние дорожки.
        "-sn",
        "-dn",

        str(output_path),
    ]

    print(
        f"Preparing iPhone-compatible MP4: "
        f"{source_path.name}",
        flush=True,
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )

    if result.returncode != 0:
        print(
            result.stderr[-5000:],
            flush=True,
        )

        raise RuntimeError(
            "Не удалось подготовить видео для iPhone"
        )

    if (
        not output_path.exists()
        or output_path.stat().st_size == 0
    ):
        raise FileNotFoundError(
            "Подготовленный видеофайл не найден"
        )

    try:
        source_path.unlink()
    except OSError:
        pass

    print(
        f"iPhone-compatible MP4 ready: "
        f"{output_path.name} | "
        f"{output_path.stat().st_size} bytes",
        flush=True,
    )

    return output_path


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[[dict[str, Any]], None],
) -> Path:
    """
    Скачивает видео со звуком и подготавливает
    совместимый с Telegram и iPhone MP4.
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

        # Сначала выбираем готовый H.264 + AAC.
        # Если его нет — скачиваем H.264 и M4A отдельно.
        "format": (
            "best[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "[acodec~='^(mp4a|aac)']/"
            
            "bestvideo[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "+bestaudio[ext=m4a]/"
            
            "best[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "[acodec!=none]/"
            
            "bestvideo[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "+bestaudio/"
            
            "best[ext=mp4]/"
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

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(
            url,
            download=True,
        )

        prepared_path = Path(
            downloader.prepare_filename(info)
        )

    downloaded_path = _find_downloaded_video(
        folder_path=folder_path,
        prepared_path=prepared_path,
        files_before=files_before,
    )

    print(
        f"Downloaded file: {downloaded_path.name} | "
        f"{downloaded_path.stat().st_size} bytes",
        flush=True,
    )

    compatible_path = _make_iphone_compatible_mp4(
        downloaded_path
    )

    if (
        compatible_path.stat().st_size
        > TELEGRAM_SAFE_SIZE
    ):
        raise RuntimeError(
            "Видео получилось больше 48 МБ. "
            "Его пока нельзя отправить через Telegram."
        )

    return compatible_path
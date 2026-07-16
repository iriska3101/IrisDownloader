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
    """Находит итоговый видеофайл после загрузки yt-dlp."""
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


def _convert_instagram_for_iphone(
    source_path: Path,
) -> Path:
    """
    Полностью перекодирует Instagram-видео
    в совместимый с Telegram и iPhone формат.
    """
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    output_path = source_path.with_name(
        f"{source_path.stem}_instagram_fixed.mp4"
    )

    command = [
        ffmpeg_path,
        "-y",

        # Создаём корректные временные метки.
        "-fflags",
        "+genpts",

        "-i",
        str(source_path),

        # Берём первую видеодорожку и звук, если он есть.
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",

        # Не переносим подозрительные метаданные.
        "-map_metadata",
        "-1",

        # Полностью пересоздаём видеодорожку.
        # Ограничиваем ширину до 720 px,
        # сохраняя исходные пропорции.
        "-vf",
        (
            "scale="
            "'min(720,iw)':"
            "-2:"
            "flags=fast_bilinear,"
            "setsar=1,"
            "fps=30"
        ),

        # Максимально совместимый видеокодек.
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "main",
        "-level",
        "4.0",
        "-threads",
        "1",

        # Совместимый звук.
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-ac",
        "2",

        # Исправляем временные метки.
        "-avoid_negative_ts",
        "make_zero",

        # Сбрасываем возможный неправильный поворот.
        "-metadata:s:v:0",
        "rotate=0",

        # Размещаем служебную информацию MP4
        # в начале файла.
        "-movflags",
        "+faststart",

        # Убираем лишние дорожки.
        "-sn",
        "-dn",

        str(output_path),
    ]

    print(
        f"Converting Instagram video for iPhone: "
        f"{source_path.name}",
        flush=True,
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )

    if result.returncode != 0:
        print(
            result.stderr[-6000:],
            flush=True,
        )

        raise RuntimeError(
            "Не удалось преобразовать Instagram-видео"
        )

    if (
        not output_path.exists()
        or output_path.stat().st_size == 0
    ):
        raise FileNotFoundError(
            "Преобразованный видеофайл не найден"
        )

    print(
        f"Instagram video ready: "
        f"{output_path.name} | "
        f"{output_path.stat().st_size} bytes",
        flush=True,
    )

    try:
        source_path.unlink()
    except OSError:
        pass

    return output_path


def _remux_other_video(
    source_path: Path,
) -> Path:
    """
    Быстро пересобирает контейнер остальных видео,
    не перекодируя изображение.
    """
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    output_path = source_path.with_name(
        f"{source_path.stem}_clean.mp4"
    )

    command = [
        ffmpeg_path,
        "-y",

        "-fflags",
        "+genpts",

        "-i",
        str(source_path),

        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",

        "-map_metadata",
        "-1",

        # Изображение не пережимаем.
        "-c:v",
        "copy",

        # Звук приводим к AAC.
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-ac",
        "2",

        "-avoid_negative_ts",
        "make_zero",

        "-metadata:s:v:0",
        "rotate=0",

        "-movflags",
        "+faststart",

        "-sn",
        "-dn",

        str(output_path),
    ]

    print(
        f"Preparing video container: {source_path.name}",
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
            "Не удалось подготовить видео для Telegram"
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

    return output_path


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[[dict[str, Any]], None],
) -> Path:
    """
    Скачивает видео со звуком.

    Instagram полностью преобразуется в H.264 + AAC.
    Остальные сайты обрабатываются без тяжёлого
    перекодирования изображения.
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

        # В первую очередь выбираем H.264 со звуком.
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

    normalized_url = url.lower()

    if "instagram.com" in normalized_url:
        final_path = _convert_instagram_for_iphone(
            downloaded_path
        )
    else:
        final_path = _remux_other_video(
            downloaded_path
        )

    if final_path.stat().st_size > TELEGRAM_SAFE_SIZE:
        raise RuntimeError(
            "Видео получилось больше 48 МБ. "
            "Его пока нельзя отправить через Telegram."
        )

    return final_path
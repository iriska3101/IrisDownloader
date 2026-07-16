import os
from pathlib import Path
from typing import Any, Callable

import imageio_ffmpeg
import yt_dlp


TELEGRAM_SAFE_SIZE = 48 * 1024 * 1024

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
}


def _print_available_formats(
    info: dict[str, Any],
) -> None:
    """Печатает доступные форматы в Render Logs."""
    print(
        "\n========== AVAILABLE FORMATS ==========",
        flush=True,
    )

    for item in info.get("formats") or []:
        if not isinstance(item, dict):
            continue

        print(
            "ID={format_id} | EXT={ext} | "
            "SIZE={width}x{height} | "
            "VCODEC={vcodec} | ACODEC={acodec} | "
            "FPS={fps} | TBR={tbr} | "
            "NOTE={format_note}".format(
                format_id=item.get("format_id"),
                ext=item.get("ext"),
                width=item.get("width"),
                height=item.get("height"),
                vcodec=item.get("vcodec"),
                acodec=item.get("acodec"),
                fps=item.get("fps"),
                tbr=item.get("tbr"),
                format_note=item.get("format_note"),
            ),
            flush=True,
        )

    print(
        "=======================================\n",
        flush=True,
    )


def _print_selected_format(
    info: dict[str, Any],
) -> None:
    """Печатает формат, который реально выбрал yt-dlp."""
    requested_formats = (
        info.get("requested_formats")
        or []
    )

    if requested_formats:
        print(
            "========== SELECTED STREAMS ==========",
            flush=True,
        )

        for item in requested_formats:
            if not isinstance(item, dict):
                continue

            print(
                "ID={format_id} | EXT={ext} | "
                "SIZE={width}x{height} | "
                "VCODEC={vcodec} | ACODEC={acodec}".format(
                    format_id=item.get("format_id"),
                    ext=item.get("ext"),
                    width=item.get("width"),
                    height=item.get("height"),
                    vcodec=item.get("vcodec"),
                    acodec=item.get("acodec"),
                ),
                flush=True,
            )

        print(
            "======================================",
            flush=True,
        )

    else:
        print(
            "SELECTED FORMAT: "
            f"ID={info.get('format_id')} | "
            f"EXT={info.get('ext')} | "
            f"SIZE={info.get('width')}x{info.get('height')} | "
            f"VCODEC={info.get('vcodec')} | "
            f"ACODEC={info.get('acodec')}",
            flush=True,
        )


def _find_downloaded_video(
    folder_path: Path,
    prepared_path: Path,
    files_before: set[Path],
) -> Path:
    """Находит конечный файл после загрузки или объединения."""
    possible_paths = [
        prepared_path.with_suffix(".mp4"),
        prepared_path,
    ]

    for possible_path in possible_paths:
        if (
            possible_path.exists()
            and possible_path.is_file()
            and possible_path.stat().st_size > 0
        ):
            return possible_path

    candidates = [
        file
        for file in folder_path.iterdir()
        if (
            file.is_file()
            and file.resolve() not in files_before
            and file.suffix.lower() in VIDEO_EXTENSIONS
            and file.stat().st_size > 0
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


def _has_audio(
    info: dict[str, Any],
) -> bool:
    """Проверяет наличие звука в выбранном формате."""
    requested_formats = (
        info.get("requested_formats")
        or []
    )

    if requested_formats:
        for item in requested_formats:
            if not isinstance(item, dict):
                continue

            audio_codec = str(
                item.get("acodec") or ""
            ).lower()

            if audio_codec not in {
                "",
                "none",
            }:
                return True

        return False

    audio_codec = str(
        info.get("acodec") or ""
    ).lower()

    return audio_codec not in {
        "",
        "none",
    }


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[[dict[str, Any]], None],
) -> Path:
    """
    Скачивает видео со звуком без перекодирования.

    Сначала выбирает готовый MP4 со встроенным звуком.
    Если такого файла нет, скачивает лучшую видеодорожку
    и лучшую аудиодорожку и объединяет их в MP4.
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

    inspect_options: dict[str, Any] = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 120,
        "retries": 5,
        "fragment_retries": 5,
    }

    print(
        f"Inspecting video: {url}",
        flush=True,
    )

    with yt_dlp.YoutubeDL(
        inspect_options
    ) as inspector:
        inspected_info = inspector.extract_info(
            url,
            download=False,
        )

    _print_available_formats(
        inspected_info
    )

    options: dict[str, Any] = {
        "outtmpl": template,

        # 1. Готовый MP4, где уже есть видео и звук.
        # 2. Любой готовый файл с видео и звуком.
        # 3. Отдельные видео + аудио.
        # 4. Стандартный запасной вариант.
        "format": (
            "best[ext=mp4]"
            "[vcodec!=none]"
            "[acodec!=none]/"

            "best"
            "[vcodec!=none]"
            "[acodec!=none]/"

            "bestvideo*+bestaudio/"
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
        "Downloading original video with audio...",
        flush=True,
    )

    with yt_dlp.YoutubeDL(
        options
    ) as downloader:
        downloaded_info = downloader.extract_info(
            url,
            download=True,
        )

        prepared_path = Path(
            downloader.prepare_filename(
                downloaded_info
            )
        )

    _print_selected_format(
        downloaded_info
    )

    if not _has_audio(downloaded_info):
        raise RuntimeError(
            "Скачанный формат не содержит звука"
        )

    downloaded_path = _find_downloaded_video(
        folder_path=folder_path,
        prepared_path=prepared_path,
        files_before=files_before,
    )

    file_size = downloaded_path.stat().st_size

    print(
        f"Final original video: "
        f"{downloaded_path.name} | "
        f"{file_size} bytes",
        flush=True,
    )

    if file_size > TELEGRAM_SAFE_SIZE:
        raise RuntimeError(
            "Видео получилось больше 48 МБ. "
            "Telegram не разрешит боту отправить его."
        )

    return downloaded_path
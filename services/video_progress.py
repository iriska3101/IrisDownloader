import os
from pathlib import Path
from typing import Any, Callable

import yt_dlp


TELEGRAM_SAFE_SIZE = 48 * 1024 * 1024

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".m4v",
}


def _find_downloaded_video(
    folder_path: Path,
    prepared_path: Path,
    files_before: set[Path],
) -> Path:
    """
    Находит файл, который реально скачал yt-dlp.
    Никакой обработки или перекодирования не выполняется.
    """
    if (
        prepared_path.exists()
        and prepared_path.is_file()
        and prepared_path.stat().st_size > 0
    ):
        return prepared_path

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
            "После загрузки видеофайл не найден"
        )

    return max(
        candidates,
        key=lambda item: (
            item.stat().st_mtime,
            item.stat().st_size,
        ),
    )


def _print_download_info(
    info: dict[str, Any],
    video_path: Path,
) -> None:
    """
    Выводит в Render Logs информацию о скачанном файле.
    """
    print(
        "\n========== IRISSAVE DIAGNOSTIC ==========",
        flush=True,
    )

    print(
        f"File: {video_path.name}",
        flush=True,
    )

    print(
        f"Size: {video_path.stat().st_size} bytes",
        flush=True,
    )

    print(
        f"Format ID: {info.get('format_id')}",
        flush=True,
    )

    print(
        f"Extension: {info.get('ext')}",
        flush=True,
    )

    print(
        f"Resolution: "
        f"{info.get('width')}x{info.get('height')}",
        flush=True,
    )

    print(
        f"Video codec: {info.get('vcodec')}",
        flush=True,
    )

    print(
        f"Audio codec: {info.get('acodec')}",
        flush=True,
    )

    requested_formats = (
        info.get("requested_formats")
        or []
    )

    if requested_formats:
        print(
            "Requested formats:",
            flush=True,
        )

        for item in requested_formats:
            if not isinstance(item, dict):
                continue

            print(
                "  "
                f"ID={item.get('format_id')} | "
                f"EXT={item.get('ext')} | "
                f"VCODEC={item.get('vcodec')} | "
                f"ACODEC={item.get('acodec')} | "
                f"SIZE={item.get('width')}x"
                f"{item.get('height')}",
                flush=True,
            )

    print(
        "=========================================\n",
        flush=True,
    )


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[
        [dict[str, Any]],
        None,
    ],
) -> Path:
    """
    Диагностическая загрузка.

    Файл скачивается через yt-dlp и возвращается без:
    - FFmpeg;
    - перекодирования;
    - изменения пропорций;
    - проверки аудиодорожки;
    - повторной загрузки.
    """
    folder_path = Path(folder)

    folder_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    template = os.path.join(
        folder,
        "diagnostic-%(title).70s-%(id)s.%(ext)s",
    )

    files_before = {
        file.resolve()
        for file in folder_path.iterdir()
        if file.is_file()
    }

    options: dict[str, Any] = {
        "outtmpl": template,

        # Специально не задаём format.
        # yt-dlp сам выберет стандартный формат.
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,

        "socket_timeout": 60,
        "retries": 2,
        "fragment_retries": 2,

        "continuedl": True,
        "overwrites": True,

        "progress_hooks": [
            progress_hook
        ],
    }

    try:
        with yt_dlp.YoutubeDL(
            options
        ) as downloader:
            info = downloader.extract_info(
                url,
                download=True,
            )

            if not isinstance(info, dict):
                raise RuntimeError(
                    "yt-dlp не вернул информацию о видео"
                )

            prepared_path = Path(
                downloader.prepare_filename(info)
            )

    except yt_dlp.utils.DownloadError as error:
        raise RuntimeError(
            str(error)
        ) from error

    video_path = _find_downloaded_video(
        folder_path=folder_path,
        prepared_path=prepared_path,
        files_before=files_before,
    )

    _print_download_info(
        info=info,
        video_path=video_path,
    )

    if (
        video_path.stat().st_size
        > TELEGRAM_SAFE_SIZE
    ):
        raise RuntimeError(
            "Диагностический файл больше 48 МБ"
        )

    return video_path
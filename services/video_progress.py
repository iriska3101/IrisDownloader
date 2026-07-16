import os
from pathlib import Path
from typing import Any, Callable

import yt_dlp


def _print_formats(info: dict[str, Any]) -> None:
    """Печатает доступные форматы ролика в Render Logs."""
    print("\n========== AVAILABLE FORMATS ==========", flush=True)

    for item in info.get("formats") or []:
        print(
            "ID={id} | EXT={ext} | SIZE={width}x{height} | "
            "VCODEC={vcodec} | ACODEC={acodec} | "
            "ABR={abr} | TBR={tbr}".format(
                id=item.get("format_id"),
                ext=item.get("ext"),
                width=item.get("width"),
                height=item.get("height"),
                vcodec=item.get("vcodec"),
                acodec=item.get("acodec"),
                abr=item.get("abr"),
                tbr=item.get("tbr"),
            ),
            flush=True,
        )

    print("=======================================\n", flush=True)


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[[dict[str, Any]], None],
) -> Path:
    """
    Предпочитает TikTok-видео H.264 со встроенным звуком.
    Избегает проблемных HEVC/bytevc1-вариантов.
    """
    folder_path = Path(folder)
    folder_path.mkdir(parents=True, exist_ok=True)

    template = os.path.join(
        folder,
        "%(title).80s-%(id)s.%(ext)s",
    )

    common_options: dict[str, Any] = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 120,
        "retries": 5,
        "fragment_retries": 5,
    }

    print(f"Checking TikTok formats: {url}", flush=True)

    # Сначала только получаем список доступных форматов.
    with yt_dlp.YoutubeDL(common_options) as inspector:
        info = inspector.extract_info(
            url,
            download=False,
        )

    _print_formats(info)

    options: dict[str, Any] = {
        **common_options,
        "outtmpl": template,

        # Сначала готовый MP4 H.264 со звуком.
        # HEVC / bytevc1 намеренно не выбираем.
        "format": (
            "best[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "[acodec!=none]/"
            "best[vcodec~='^(avc1|h264)']"
            "[acodec!=none]/"
            "best[ext=mp4]"
            "[vcodec!=none]"
            "[acodec!=none]/"
            "best[vcodec!=none]"
            "[acodec!=none]"
        ),

        "restrictfilenames": True,
        "progress_hooks": [progress_hook],
    }

    files_before = {
        file.resolve()
        for file in folder_path.iterdir()
        if file.is_file()
    }

    print("Downloading preferred H.264 format...", flush=True)

    with yt_dlp.YoutubeDL(options) as downloader:
        downloaded_info = downloader.extract_info(
            url,
            download=True,
        )

        prepared_path = Path(
            downloader.prepare_filename(downloaded_info)
        )

    if prepared_path.exists():
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
        f"Selected video: {downloaded_path.name} | "
        f"{downloaded_path.stat().st_size} bytes",
        flush=True,
    )

    return downloaded_path
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import imageio_ffmpeg
import yt_dlp


def _print_formats(info: dict[str, Any]) -> None:
    """Печатает доступные форматы в Render Logs."""
    print(
        "\n========== AVAILABLE FORMATS ==========",
        flush=True,
    )

    for item in info.get("formats") or []:
        print(
            "ID={id} | EXT={ext} | SIZE={width}x{height} | "
            "VCODEC={vcodec} | ACODEC={acodec}".format(
                id=item.get("format_id"),
                ext=item.get("ext"),
                width=item.get("width"),
                height=item.get("height"),
                vcodec=item.get("vcodec"),
                acodec=item.get("acodec"),
            ),
            flush=True,
        )

    print(
        "=======================================\n",
        flush=True,
    )


def _fix_tiktok_video(
    source_path: Path,
) -> Path:
    """
    Исправляет растянутую картинку TikTok.

    Убирает неправильные метаданные,
    сохраняет пропорции и переводит видео в H.264.
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

        # Берём первую видеодорожку и звук, если он есть.
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",

        # Убираем подозрительные метаданные исходного файла.
        "-map_metadata",
        "-1",

        # Сохраняем правильное соотношение сторон.
        # Максимальная ширина — 720 пикселей.
        "-vf",
        "scale='min(720,iw)':-2:flags=fast_bilinear,"
        "setsar=1",

        # Совместимый с Telegram видеокодек.
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "27",
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "1",

        # Сохраняем звук.
        "-c:a",
        "aac",
        "-b:a",
        "128k",

        # Убираем метаданные поворота.
        "-metadata:s:v:0",
        "rotate=0",

        # Видео сможет запускаться до полной загрузки.
        "-movflags",
        "+faststart",

        str(fixed_path),
    ]

    print(
        "Fixing TikTok proportions...",
        flush=True,
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        print(
            result.stderr[-4000:],
            flush=True,
        )

        raise RuntimeError(
            "Не удалось исправить пропорции видео"
        )

    if (
        not fixed_path.exists()
        or fixed_path.stat().st_size == 0
    ):
        raise FileNotFoundError(
            "Исправленный видеофайл не найден"
        )

    print(
        f"Fixed TikTok video: {fixed_path.name} | "
        f"{fixed_path.stat().st_size} bytes",
        flush=True,
    )

    try:
        source_path.unlink()
    except OSError:
        pass

    return fixed_path


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[[dict[str, Any]], None],
) -> Path:
    """
    Скачивает видео со звуком.

    TikTok дополнительно обрабатывается для правильного
    отображения пропорций в Telegram.
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

    common_options: dict[str, Any] = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 120,
        "retries": 5,
        "fragment_retries": 5,
    }

    print(
        f"Checking video formats: {url}",
        flush=True,
    )

    with yt_dlp.YoutubeDL(
        common_options
    ) as inspector:
        inspected_info = inspector.extract_info(
            url,
            download=False,
        )

    _print_formats(inspected_info)

    options: dict[str, Any] = {
        **common_options,

        "outtmpl": template,
        "ffmpeg_location": ffmpeg_path,
        "merge_output_format": "mp4",

        # Сначала готовое H.264-видео со звуком.
        # Затем отдельные видео и аудиодорожка.
        "format": (
            "best[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "[acodec!=none]/"

            "bestvideo[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "+bestaudio/"

            "bestvideo+bestaudio/"
            "best[ext=mp4]/"
            "best"
        ),

        "restrictfilenames": True,
        "progress_hooks": [progress_hook],
    }

    files_before = {
        file.resolve()
        for file in folder_path.iterdir()
        if file.is_file()
    }

    print(
        "Downloading preferred video and audio...",
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

    possible_mp4_path = (
        prepared_path.with_suffix(".mp4")
    )

    if possible_mp4_path.exists():
        downloaded_path = possible_mp4_path

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
                in {
                    ".mp4",
                    ".mov",
                    ".mkv",
                    ".webm",
                }
            )
        ]

        if not new_files:
            raise FileNotFoundError(
                "Скачанный видеофайл не найден"
            )

        downloaded_path = max(
            new_files,
            key=lambda item: (
                item.stat().st_mtime
            ),
        )

    print(
        f"Selected video: {downloaded_path.name} | "
        f"{downloaded_path.stat().st_size} bytes",
        flush=True,
    )

    normalized_url = url.lower()

    if "tiktok.com" in normalized_url:
        return _fix_tiktok_video(
            downloaded_path
        )

    return downloaded_path
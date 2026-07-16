import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import imageio_ffmpeg
import yt_dlp


PROBLEM_VIDEO_CODECS = (
    "hevc",
    "h265",
    "hvc1",
    "hev1",
    "bytevc1",
)


def _print_formats(info: dict[str, Any]) -> None:
    """Печатает доступные форматы ролика в Render Logs."""
    print(
        "\n========== AVAILABLE FORMATS ==========",
        flush=True,
    )

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

    print(
        "=======================================\n",
        flush=True,
    )


def _get_video_codec(info: dict[str, Any]) -> str:
    """Определяет кодек выбранной видеодорожки."""
    requested_formats = (
        info.get("requested_formats")
        or []
    )

    for item in requested_formats:
        if not isinstance(item, dict):
            continue

        video_codec = str(
            item.get("vcodec") or ""
        ).lower()

        if video_codec not in {
            "",
            "none",
        }:
            return video_codec

    return str(
        info.get("vcodec") or ""
    ).lower()


def _is_problem_codec(codec: str) -> bool:
    """Проверяет, может ли Telegram неправильно показать кодек."""
    normalized_codec = codec.lower()

    return any(
        marker in normalized_codec
        for marker in PROBLEM_VIDEO_CODECS
    )


def _convert_hevc_to_h264(
    source_path: Path,
) -> Path:
    """
    Переводит только проблемный HEVC-файл в H.264.

    Видео уменьшается максимум до 720 пикселей по ширине,
    чтобы не перегружать бесплатный Render.
    """
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    converted_path = source_path.with_name(
        f"{source_path.stem}_telegram.mp4"
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
        (
            "scale="
            "'min(720,iw)':"
            "-2:"
            "flags=fast_bilinear,"
            "setsar=1"
        ),

        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "1",

        "-c:a",
        "aac",
        "-b:a",
        "128k",

        "-movflags",
        "+faststart",

        str(converted_path),
    ]

    print(
        "Converting HEVC video to Telegram-compatible H.264...",
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
            result.stderr[-3000:],
            flush=True,
        )

        raise RuntimeError(
            "Не удалось исправить формат видео"
        )

    if (
        not converted_path.exists()
        or converted_path.stat().st_size == 0
    ):
        raise FileNotFoundError(
            "Исправленный видеофайл не найден"
        )

    print(
        f"Converted video ready: "
        f"{converted_path.name} | "
        f"{converted_path.stat().st_size} bytes",
        flush=True,
    )

    try:
        source_path.unlink()
    except OSError:
        pass

    return converted_path


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[[dict[str, Any]], None],
) -> Path:
    """
    Скачивает видео со звуком.

    Сначала выбирает H.264. Если TikTok предоставляет
    только HEVC, конвертирует именно этот ролик в H.264.
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

        # Приоритет:
        # 1. готовый H.264 MP4 со звуком;
        # 2. отдельное H.264-видео + лучшая аудиодорожка;
        # 3. любой файл со звуком как запасной вариант.
        "format": (
            "best[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "[acodec!=none]/"

            "bestvideo[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "+bestaudio/"

            "bestvideo"
            "[vcodec~='^(avc1|h264)']"
            "+bestaudio/"

            "best[ext=mp4]"
            "[vcodec!=none]"
            "[acodec!=none]/"

            "bestvideo+bestaudio/"
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
                and file.resolve()
                not in files_before
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

    selected_codec = _get_video_codec(
        downloaded_info
    )

    print(
        f"Selected video: "
        f"{downloaded_path.name} | "
        f"codec={selected_codec} | "
        f"{downloaded_path.stat().st_size} bytes",
        flush=True,
    )

    if _is_problem_codec(selected_codec):
        return _convert_hevc_to_h264(
            downloaded_path
        )

    return downloaded_path
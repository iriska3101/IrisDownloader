import os
import subprocess
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
    ".m4v",
}


def _find_downloaded_video(
    folder_path: Path,
    prepared_path: Path,
    files_before: set[Path],
) -> Path:
    """
    Находит реальный итоговый файл после скачивания
    и объединения потоков yt-dlp.
    """
    possible_paths = [
        prepared_path.with_suffix(".mp4"),
        prepared_path.with_suffix(".mkv"),
        prepared_path.with_suffix(".webm"),
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
        key=lambda item: (
            item.stat().st_mtime,
            item.stat().st_size,
        ),
    )


def _has_audio_stream(
    info: dict[str, Any],
) -> bool:
    """
    Проверяет, выбрал ли yt-dlp хотя бы один поток со звуком.
    """
    requested_formats = (
        info.get("requested_formats")
        or info.get("requested_downloads")
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

    audio_codec = str(
        info.get("acodec") or ""
    ).lower()

    return audio_codec not in {
        "",
        "none",
    }


def _normalize_video(
    source_path: Path,
    folder_path: Path,
) -> Path:
    """
    Создаёт Telegram-совместимый MP4:

    - H.264;
    - AAC;
    - квадратные пиксели;
    - чётные размеры;
    - faststart.
    """
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    output_path = (
        folder_path
        / "IriSSave_video.mp4"
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
            "setsar=1,"
            "scale="
            "trunc(iw/2)*2:"
            "trunc(ih/2)*2"
        ),

        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "48000",

        "-movflags",
        "+faststart",

        "-max_muxing_queue_size",
        "4096",

        str(output_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )

    if (
        result.returncode != 0
        or not output_path.exists()
        or output_path.stat().st_size == 0
    ):
        error = (
            result.stderr.strip()
            or "FFmpeg не смог обработать видео"
        )

        raise RuntimeError(
            "Не удалось подготовить видео "
            "для Telegram.\n"
            f"Причина: {error[-1800:]}"
        )

    return output_path


def _create_smaller_video(
    source_path: Path,
    folder_path: Path,
) -> Path:
    """
    Создаёт уменьшенную версию, если итоговый файл
    превышает лимит Telegram.
    """
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    output_path = (
        folder_path
        / "IriSSave_video_small.mp4"
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
            "setsar=1,"
            "scale="
            "'min(720,iw)':"
            "-2"
        ),

        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-maxrate",
        "1400k",
        "-bufsize",
        "2800k",
        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "aac",
        "-b:a",
        "96k",

        "-movflags",
        "+faststart",

        "-max_muxing_queue_size",
        "4096",

        str(output_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )

    if (
        result.returncode != 0
        or not output_path.exists()
        or output_path.stat().st_size == 0
    ):
        error = (
            result.stderr.strip()
            or "FFmpeg не смог уменьшить видео"
        )

        raise RuntimeError(
            "Не удалось уменьшить видео.\n"
            f"Причина: {error[-1800:]}"
        )

    return output_path


def _build_download_options(
    template: str,
    ffmpeg_path: str,
    progress_hook: Callable[
        [dict[str, Any]],
        None,
    ],
    impersonate: str | None,
) -> dict[str, Any]:
    """
    Формирует настройки yt-dlp.

    Первый запуск выполняется с имитацией Chrome.
    При несовместимости делается резервная попытка
    без принудительной имитации.
    """
    options: dict[str, Any] = {
        "outtmpl": template,

        "format": (
            "bestvideo[height<=1080]"
            "+bestaudio/"
            "best[height<=1080]"
            "[vcodec!=none]"
            "[acodec!=none]/"
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
        "file_access_retries": 3,
        "concurrent_fragment_downloads": 3,

        "continuedl": True,
        "overwrites": True,

        "progress_hooks": [
            progress_hook
        ],

        "http_headers": {
            "Accept-Language": (
                "en-US,en;q=0.9"
            ),
        },

        "extractor_args": {
            "youtube": {
                "player_client": [
                    "web",
                    "web_safari",
                    "android_vr",
                ],
            },
        },
    }

    if impersonate:
        options["impersonate"] = impersonate

    return options


def _download_with_options(
    url: str,
    options: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    """
    Выполняет загрузку и возвращает данные yt-dlp
    вместе с подготовленным путём.
    """
    with yt_dlp.YoutubeDL(
        options
    ) as downloader:
        downloaded_info = (
            downloader.extract_info(
                url,
                download=True,
            )
        )

        if not isinstance(
            downloaded_info,
            dict,
        ):
            raise RuntimeError(
                "Сервис не вернул "
                "информацию о видео"
            )

        prepared_path = Path(
            downloader.prepare_filename(
                downloaded_info
            )
        )

    return downloaded_info, prepared_path


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[
        [dict[str, Any]],
        None,
    ],
) -> Path:
    """
    Скачивает видео со звуком и приводит его
    к формату, совместимому с Telegram.
    """
    folder_path = Path(folder)
    folder_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    template = os.path.join(
        folder,
        "source-%(title).70s-%(id)s.%(ext)s",
    )

    ffmpeg_path = (
        imageio_ffmpeg.get_ffmpeg_exe()
    )

    files_before = {
        file.resolve()
        for file in folder_path.iterdir()
        if file.is_file()
    }

    chrome_options = _build_download_options(
        template=template,
        ffmpeg_path=ffmpeg_path,
        progress_hook=progress_hook,
        impersonate="chrome",
    )

    normal_options = _build_download_options(
        template=template,
        ffmpeg_path=ffmpeg_path,
        progress_hook=progress_hook,
        impersonate=None,
    )

    first_error: Exception | None = None

    try:
        downloaded_info, prepared_path = (
            _download_with_options(
                url=url,
                options=chrome_options,
            )
        )

    except Exception as error:
        first_error = error

        print(
            "Chrome impersonation failed: "
            f"{error}",
            flush=True,
        )

        try:
            downloaded_info, prepared_path = (
                _download_with_options(
                    url=url,
                    options=normal_options,
                )
            )

        except yt_dlp.utils.DownloadError as error:
            error_text = str(error)

            if (
                "Sign in to confirm"
                in error_text
                or "not a bot"
                in error_text
            ):
                raise RuntimeError(
                    "YouTube заблокировал загрузку "
                    "с сервера Render.\n"
                    "Для YouTube понадобятся cookies "
                    "или другой сервер."
                ) from error

            if "TikTok" in error_text:
                raise RuntimeError(
                    "TikTok не отдал видео серверу Render.\n"
                    "Возможна блокировка серверного IP "
                    "или временное ограничение TikTok.\n"
                    f"Причина: {error_text}"
                ) from error

            if "Instagram" in error_text:
                raise RuntimeError(
                    "Instagram не отдал данные публикации "
                    "серверу Render.\n"
                    "Возможно, понадобятся cookies "
                    "Instagram.\n"
                    f"Причина: {error_text}"
                ) from error

            raise RuntimeError(
                error_text
            ) from error

        except Exception as error:
            raise RuntimeError(
                "Не удалось скачать видео "
                "ни с имитацией Chrome, "
                "ни обычным способом.\n"
                f"Первая ошибка: {first_error}\n"
                f"Вторая ошибка: {error}"
            ) from error

    if not _has_audio_stream(
        downloaded_info
    ):
        raise RuntimeError(
            "Сервис не предоставил "
            "аудиодорожку для этого видео"
        )

    downloaded_path = _find_downloaded_video(
        folder_path=folder_path,
        prepared_path=prepared_path,
        files_before=files_before,
    )

    normalized_path = _normalize_video(
        source_path=downloaded_path,
        folder_path=folder_path,
    )

    if (
        normalized_path.stat().st_size
        <= TELEGRAM_SAFE_SIZE
    ):
        return normalized_path

    smaller_path = _create_smaller_video(
        source_path=normalized_path,
        folder_path=folder_path,
    )

    if (
        smaller_path.stat().st_size
        > TELEGRAM_SAFE_SIZE
    ):
        raise RuntimeError(
            "Видео даже после уменьшения "
            "осталось больше 48 МБ"
        )

    return smaller_path
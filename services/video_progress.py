import html
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx
import imageio_ffmpeg
import yt_dlp

from config import BROWSER_HEADERS
from services.downloader import (
    extract_json_objects,
    first_url,
    resolve_tiktok_url,
)
from services.formats.common import get_platform_options


TELEGRAM_SAFE_SIZE = 48 * 1024 * 1024

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".m4v",
}


def _safe_url(url: str) -> str:
    """
    Убирает query и fragment перед выводом URL в Render Logs.
    """
    try:
        parts = urlsplit(url)

        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                "",
                "",
            )
        )

    except Exception:
        return url[:300]


def _find_downloaded_video(
    folder_path: Path,
    prepared_path: Path,
    files_before: set[Path],
) -> Path:
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
    platform: str,
    info: dict[str, Any],
    video_path: Path,
) -> None:
    print(
        "\n========== IRISSAVE DIAGNOSTIC ==========",
        flush=True,
    )

    print(
        f"Platform: {platform}",
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


def _extract_tiktok_video_urls_from_data(
    data: Any,
) -> list[str]:
    preferred: list[str] = []
    fallback: list[str] = []

    play_keys = {
        "playaddr",
        "playurl",
        "playuri",
        "play",
    }

    download_keys = {
        "downloadaddr",
        "downloadurl",
        "downloaduri",
    }

    def normalize(key: str) -> str:
        return (
            key.replace("-", "")
            .replace("_", "")
            .lower()
        )

    def walk(
        value: Any,
        inside_video: bool = False,
    ) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = normalize(
                    str(key)
                )

                now_inside_video = (
                    inside_video
                    or normalized
                    in {
                        "video",
                        "videoinfo",
                        "videodetail",
                        "videoresource",
                        "bitrateinfo",
                        "bitrateinfos",
                    }
                )

                if now_inside_video:
                    if normalized in play_keys:
                        candidate = first_url(
                            child
                        )

                        if candidate:
                            preferred.append(
                                candidate
                            )

                    elif normalized in download_keys:
                        candidate = first_url(
                            child
                        )

                        if candidate:
                            fallback.append(
                                candidate
                            )

                walk(
                    child,
                    now_inside_video,
                )

        elif isinstance(value, list):
            for child in value:
                walk(
                    child,
                    inside_video,
                )

    walk(data)

    return list(
        dict.fromkeys(
            preferred + fallback
        )
    )


def _extract_tiktok_video_urls_from_html(
    page_html: str,
) -> list[str]:
    decoded = html.unescape(
        page_html
    )

    decoded = decoded.replace(
        "\\u002F",
        "/",
    )

    decoded = decoded.replace(
        "\\u0026",
        "&",
    )

    decoded = decoded.replace(
        "\\/",
        "/",
    )

    patterns = [
        r'"playAddr"\s*:\s*"([^"]+)"',
        r'"playUrl"\s*:\s*"([^"]+)"',
        r'"downloadAddr"\s*:\s*"([^"]+)"',
    ]

    found: list[str] = []

    for pattern in patterns:
        for match in re.findall(
            pattern,
            decoded,
            flags=re.IGNORECASE,
        ):
            candidate = (
                match
                .replace("\\u002F", "/")
                .replace("\\u0026", "&")
                .replace("\\/", "/")
            )

            if candidate.startswith(
                "http"
            ):
                found.append(
                    candidate
                )

    return list(
        dict.fromkeys(
            found
        )
    )


def _looks_like_mp4(
    path: Path,
) -> bool:
    try:
        with path.open(
            "rb"
        ) as file:
            header = file.read(
                4096
            )

    except OSError:
        return False

    return b"ftyp" in header


def _validate_video(
    path: Path,
) -> tuple[bool, str]:
    if not path.exists():
        return (
            False,
            "файл не существует",
        )

    if not path.is_file():
        return (
            False,
            "путь не является файлом",
        )

    size = (
        path.stat().st_size
    )

    if size < 100_000:
        return (
            False,
            "слишком маленький файл: "
            f"{size} bytes",
        )

    has_ftyp = _looks_like_mp4(
        path
    )

    if not has_ftyp:
        print(
            "validation-note="
            "MP4-сигнатура ftyp не найдена в первых 4096 байтах; "
            "проверяю файл через ffmpeg",
            flush=True,
        )

    ffmpeg = (
        imageio_ffmpeg
        .get_ffmpeg_exe()
    )

    command = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )

    except OSError as error:
        return (
            False,
            "ffmpeg не запустился: "
            f"{type(error).__name__}: "
            f"{error}",
        )

    except subprocess.TimeoutExpired:
        return (
            False,
            "ffmpeg не завершил "
            "проверку за 30 секунд",
        )

    if result.returncode != 0:
        ffmpeg_error = (
            result.stderr.strip()
            or (
                "ffmpeg return code "
                f"{result.returncode}"
            )
        )

        return (
            False,
            "ffmpeg отклонил видео: "
            f"{ffmpeg_error[-1200:]}",
        )

    return (
        True,
        "ok (ffmpeg; ftyp="
        f"{'yes' if has_ftyp else 'no'})",
    )


def _download_tiktok_direct(
    url: str,
    folder_path: Path,
    progress_hook: Callable[
        [dict[str, Any]],
        None,
    ],
) -> Path:
    resolved_url = (
        resolve_tiktok_url(
            url
        )
    )

    print(
        "TIKTOK DIRECT PAGE: "
        f"source={_safe_url(url)}",
        flush=True,
    )

    print(
        "TIKTOK DIRECT PAGE: "
        f"resolved="
        f"{_safe_url(resolved_url)}",
        flush=True,
    )

    timeout = httpx.Timeout(
        connect=20.0,
        read=60.0,
        write=20.0,
        pool=20.0,
    )

    with httpx.Client(
        headers=BROWSER_HEADERS,
        follow_redirects=True,
        timeout=timeout,
    ) as client:
        response = client.get(
            resolved_url
        )

        response.raise_for_status()

        final_url = str(
            response.url
        )

        page_html = (
            response.text
        )

    print(
        "TIKTOK DIRECT PAGE: "
        f"final={_safe_url(final_url)} | "
        f"status={response.status_code} | "
        "content-type="
        f"{response.headers.get('content-type', '')}",
        flush=True,
    )

    video_urls: list[str] = []

    for data in extract_json_objects(
        page_html
    ):
        video_urls.extend(
            _extract_tiktok_video_urls_from_data(
                data
            )
        )

    video_urls.extend(
        _extract_tiktok_video_urls_from_html(
            page_html
        )
    )

    video_urls = list(
        dict.fromkeys(
            video_urls
        )
    )

    if not video_urls:
        raise RuntimeError(
            "TikTok не отдал "
            "прямой адрес видео"
        )

    print(
        "TIKTOK DIRECT: "
        "найдено кандидатов: "
        f"{len(video_urls)}",
        flush=True,
    )

    headers = {
        **BROWSER_HEADERS,
        "Referer": final_url,
    }

    valid_candidates: list[
        Path
    ] = []

    last_error: (
        Exception | None
    ) = None

    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(
            connect=20.0,
            read=120.0,
            write=20.0,
            pool=20.0,
        ),
    ) as client:
        for index, video_url in enumerate(
            video_urls,
            start=1,
        ):
            path = (
                folder_path
                / (
                    "tiktok-direct-"
                    f"{index}.mp4"
                )
            )

            try:
                print(
                    "\n---------- "
                    "TIKTOK CANDIDATE "
                    f"{index}/"
                    f"{len(video_urls)} "
                    "----------",
                    flush=True,
                )

                print(
                    "request="
                    f"{_safe_url(video_url)}",
                    flush=True,
                )

                with client.stream(
                    "GET",
                    video_url,
                ) as media_response:
                    media_response.raise_for_status()

                    response_final_url = str(
                        media_response.url
                    )

                    content_type = (
                        media_response
                        .headers
                        .get(
                            "content-type",
                            "",
                        )
                        .split(
                            ";",
                            1,
                        )[0]
                        .strip()
                        .lower()
                    )

                    content_length_raw = (
                        media_response
                        .headers
                        .get(
                            "content-length",
                            "",
                        )
                    )

                    content_range = (
                        media_response
                        .headers
                        .get(
                            "content-range",
                            "",
                        )
                    )

                    print(
                        "status="
                        f"{media_response.status_code}",
                        flush=True,
                    )

                    print(
                        "final="
                        f"{_safe_url(response_final_url)}",
                        flush=True,
                    )

                    print(
                        "content-type="
                        f"{content_type or 'unknown'}",
                        flush=True,
                    )

                    print(
                        "content-length="
                        f"{content_length_raw or 'unknown'}",
                        flush=True,
                    )

                    if content_range:
                        print(
                            "content-range="
                            f"{content_range}",
                            flush=True,
                        )

                    if content_type.startswith(
                        (
                            "text/",
                            "application/json",
                        )
                    ):
                        print(
                            "result=REJECTED: "
                            "сервер вернул "
                            "текст/JSON",
                            flush=True,
                        )

                        continue

                    try:
                        total = int(
                            content_length_raw
                            or 0
                        )

                    except ValueError:
                        total = 0

                    if (
                        total
                        and total
                        > TELEGRAM_SAFE_SIZE
                    ):
                        print(
                            "result=REJECTED: "
                            "кандидат больше "
                            "48 МБ",
                            flush=True,
                        )

                        continue

                    downloaded = 0

                    with path.open(
                        "wb"
                    ) as file:
                        for chunk in (
                            media_response
                            .iter_bytes(
                                chunk_size=(
                                    256
                                    * 1024
                                )
                            )
                        ):
                            if not chunk:
                                continue

                            file.write(
                                chunk
                            )

                            downloaded += (
                                len(chunk)
                            )

                            if (
                                downloaded
                                > TELEGRAM_SAFE_SIZE
                            ):
                                raise RuntimeError(
                                    "Кандидат TikTok "
                                    "превысил 48 МБ"
                                )

                size = (
                    path.stat().st_size
                    if path.exists()
                    else 0
                )

                print(
                    "downloaded-bytes="
                    f"{size}",
                    flush=True,
                )

                (
                    valid,
                    validation_reason,
                ) = _validate_video(
                    path
                )

                print(
                    "validation="
                    f"{'VALID' if valid else 'INVALID'}",
                    flush=True,
                )

                print(
                    "validation-reason="
                    f"{validation_reason}",
                    flush=True,
                )

                if not valid:
                    path.unlink(
                        missing_ok=True
                    )

                    continue

                valid_candidates.append(
                    path
                )

            except (
                httpx.HTTPError,
                OSError,
                RuntimeError,
            ) as error:
                last_error = error

                path.unlink(
                    missing_ok=True
                )

                print(
                    "result=ERROR: "
                    f"{type(error).__name__}: "
                    f"{error}",
                    flush=True,
                )

    if not valid_candidates:
        raise RuntimeError(
            "TikTok не отдал "
            "ни одного воспроизводимого "
            "видеофайла"
        ) from last_error

    best_path = max(
        valid_candidates,
        key=lambda item: (
            item.stat().st_size
        ),
    )

    for candidate in (
        valid_candidates
    ):
        if candidate != best_path:
            candidate.unlink(
                missing_ok=True
            )

    best_size = (
        best_path.stat().st_size
    )

    print(
        "\n========== "
        "TIKTOK DIRECT RESULT "
        "==========",
        flush=True,
    )

    print(
        f"selected={best_path.name}",
        flush=True,
    )

    print(
        f"size={best_size} bytes",
        flush=True,
    )

    print(
        "================================"
        "==========\n",
        flush=True,
    )

    # Прогресс отправляем только
    # после того, как файл уже
    # действительно прошёл проверку.
    progress_hook(
        {
            "status": "finished",
            "downloaded_bytes": best_size,
            "total_bytes": best_size,
            "filename": str(
                best_path
            ),
        }
    )

    return best_path


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[
        [dict[str, Any]],
        None,
    ],
) -> Path:
    folder_path = Path(
        folder
    )

    folder_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    (
        platform,
        platform_options,
    ) = get_platform_options(
        url
    )

    print(
        "IRISSAVE PLATFORM: "
        f"{platform}",
        flush=True,
    )

    if platform == "TIKTOK":
        try:
            print(
                "TIKTOK DIRECT: "
                "ищу и проверяю "
                "видеопотоки",
                flush=True,
            )

            direct_path = (
                _download_tiktok_direct(
                    url=url,
                    folder_path=folder_path,
                    progress_hook=progress_hook,
                )
            )

            return direct_path

        except Exception as direct_error:
            print(
                "\n========== "
                "TIKTOK DIRECT FAILED "
                "==========",
                flush=True,
            )

            print(
                f"{type(direct_error).__name__}: "
                f"{direct_error}",
                flush=True,
            )

            cause = (
                direct_error.__cause__
                or direct_error.__context__
            )

            if cause is not None:
                print(
                    "Cause: "
                    f"{type(cause).__name__}: "
                    f"{cause}",
                    flush=True,
                )

            print(
                "================================"
                "==========\n",
                flush=True,
            )

            print(
                "TIKTOK FALLBACK: "
                "запускаю yt-dlp",
                flush=True,
            )

    template = os.path.join(
        folder,
        (
            "diagnostic-"
            "%(title).70s-"
            "%(id)s."
            "%(ext)s"
        ),
    )

    files_before = {
        file.resolve()
        for file in (
            folder_path.iterdir()
        )
        if file.is_file()
    }

    options: dict[
        str,
        Any,
    ] = {
        "outtmpl": template,
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
            progress_hook,
        ],
        "http_headers": (
            BROWSER_HEADERS
        ),
    }

    options.update(
        platform_options
    )

    try:
        with yt_dlp.YoutubeDL(
            options
        ) as downloader:
            info = (
                downloader.extract_info(
                    url,
                    download=True,
                )
            )

            print(
                "\n========== "
                "FILES AFTER YT-DLP "
                "==========",
                flush=True,
            )

            for file_path in sorted(
                folder_path.iterdir()
            ):
                if file_path.is_file():
                    print(
                        "FILE: "
                        f"{file_path.name} | "
                        "SIZE: "
                        f"{file_path.stat().st_size} "
                        "bytes",
                        flush=True,
                    )

            print(
                "================================"
                "========\n",
                flush=True,
            )

            if not isinstance(
                info,
                dict,
            ):
                raise RuntimeError(
                    "yt-dlp не вернул "
                    "информацию о видео"
                )

            prepared_path = Path(
                downloader.prepare_filename(
                    info
                )
            )

    except (
        yt_dlp.utils.DownloadError
    ) as error:
        if platform == "TIKTOK":
            raise RuntimeError(
                "TikTok временно "
                "не отдал видео "
                "ни прямым способом, "
                "ни через yt-dlp"
            ) from error

        raise RuntimeError(
            str(error)
        ) from error

    video_path = (
        _find_downloaded_video(
            folder_path=folder_path,
            prepared_path=prepared_path,
            files_before=files_before,
        )
    )

    _print_download_info(
        platform=platform,
        info=info,
        video_path=video_path,
    )

    if (
        video_path.stat().st_size
        > TELEGRAM_SAFE_SIZE
    ):
        raise RuntimeError(
            "Диагностический файл "
            "больше 48 МБ"
        )

    return video_path
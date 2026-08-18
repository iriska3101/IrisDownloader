import html
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

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
    print(f"Platform: {platform}", flush=True)
    print(f"File: {video_path.name}", flush=True)
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
        print("Requested formats:", flush=True)

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
                normalized = normalize(str(key))

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
                        candidate = first_url(child)

                        if candidate:
                            preferred.append(candidate)

                    elif normalized in download_keys:
                        candidate = first_url(child)

                        if candidate:
                            fallback.append(candidate)

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
    decoded = html.unescape(page_html)
    decoded = decoded.replace("\\u002F", "/")
    decoded = decoded.replace("\\u0026", "&")
    decoded = decoded.replace("\\/", "/")

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
                match.replace("\\u002F", "/")
                .replace("\\u0026", "&")
                .replace("\\/", "/")
            )

            if candidate.startswith("http"):
                found.append(candidate)

    return list(dict.fromkeys(found))


def _looks_like_mp4(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            header = file.read(64)
    except OSError:
        return False

    return b"ftyp" in header


def _is_playable_video(path: Path) -> bool:
    if (
        not path.exists()
        or not path.is_file()
        or path.stat().st_size < 100_000
    ):
        return False

    if not _looks_like_mp4(path):
        return False

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    command = [
        ffmpeg,
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
    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        return False

    if result.returncode != 0:
        print(
            "TIKTOK VALIDATE: ffmpeg отклонил "
            f"{path.name}: {result.stderr[-500:]}",
            flush=True,
        )
        return False

    return True


def _download_tiktok_direct(
    url: str,
    folder_path: Path,
    progress_hook: Callable[
        [dict[str, Any]],
        None,
    ],
) -> Path:
    resolved_url = resolve_tiktok_url(url)

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
        response = client.get(resolved_url)
        response.raise_for_status()

        final_url = str(response.url)
        page_html = response.text

    video_urls: list[str] = []

    for data in extract_json_objects(page_html):
        video_urls.extend(
            _extract_tiktok_video_urls_from_data(data)
        )

    video_urls.extend(
        _extract_tiktok_video_urls_from_html(
            page_html
        )
    )

    video_urls = list(
        dict.fromkeys(video_urls)
    )

    if not video_urls:
        raise RuntimeError(
            "TikTok не отдал прямой адрес видео"
        )

    print(
        f"TIKTOK DIRECT: найдено URL: {len(video_urls)}",
        flush=True,
    )

    headers = {
        **BROWSER_HEADERS,
        "Referer": final_url,
    }

    valid_candidates: list[Path] = []
    last_error: Exception | None = None

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
                / f"tiktok-direct-{index}.mp4"
            )

            try:
                with client.stream(
                    "GET",
                    video_url,
                ) as media_response:
                    media_response.raise_for_status()

                    content_type = (
                        media_response.headers.get(
                            "content-type",
                            "",
                        )
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )

                    if content_type.startswith(
                        ("text/", "application/json")
                    ):
                        print(
                            "TIKTOK DIRECT: "
                            f"кандидат {index} пропущен, "
                            f"Content-Type={content_type}",
                            flush=True,
                        )
                        continue

                    total = int(
                        media_response.headers.get(
                            "content-length",
                            "0",
                        )
                        or 0
                    )

                    if (
                        total
                        and total > TELEGRAM_SAFE_SIZE
                    ):
                        print(
                            "TIKTOK DIRECT: "
                            f"кандидат {index} больше 48 МБ, "
                            "пропускаю",
                            flush=True,
                        )
                        continue

                    downloaded = 0

                    with path.open("wb") as file:
                        for chunk in (
                            media_response.iter_bytes(
                                chunk_size=256 * 1024
                            )
                        ):
                            if not chunk:
                                continue

                            file.write(chunk)
                            downloaded += len(chunk)

                            if downloaded > TELEGRAM_SAFE_SIZE:
                                raise RuntimeError(
                                    "Кандидат TikTok "
                                    "превысил 48 МБ"
                                )

                            progress_hook(
                                {
                                    "status": "downloading",
                                    "downloaded_bytes": downloaded,
                                    "total_bytes": total or None,
                                    "filename": str(path),
                                }
                            )

                size = (
                    path.stat().st_size
                    if path.exists()
                    else 0
                )

                print(
                    "TIKTOK DIRECT: "
                    f"кандидат {index}, "
                    f"Content-Type={content_type or 'unknown'}, "
                    f"size={size}",
                    flush=True,
                )

                if not _is_playable_video(path):
                    print(
                        "TIKTOK DIRECT: "
                        f"кандидат {index} не является "
                        "воспроизводимым MP4",
                        flush=True,
                    )
                    path.unlink(missing_ok=True)
                    continue

                print(
                    "TIKTOK DIRECT: "
                    f"кандидат {index} валидный",
                    flush=True,
                )

                valid_candidates.append(path)

            except (
                httpx.HTTPError,
                OSError,
                RuntimeError,
            ) as error:
                last_error = error
                path.unlink(missing_ok=True)

                print(
                    "TIKTOK DIRECT: "
                    f"кандидат {index} отклонён — "
                    f"{type(error).__name__}: {error}",
                    flush=True,
                )

    if not valid_candidates:
        raise RuntimeError(
            "TikTok не отдал ни одного "
            "воспроизводимого видеофайла"
        ) from last_error

    best_path = max(
        valid_candidates,
        key=lambda item: item.stat().st_size,
    )

    for candidate in valid_candidates:
        if candidate != best_path:
            candidate.unlink(missing_ok=True)

    progress_hook(
        {
            "status": "finished",
            "downloaded_bytes": best_path.stat().st_size,
            "total_bytes": best_path.stat().st_size,
            "filename": str(best_path),
        }
    )

    print(
        "TIKTOK DIRECT: выбран лучший файл — "
        f"{best_path.name}, "
        f"{best_path.stat().st_size} bytes",
        flush=True,
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
    folder_path = Path(folder)

    folder_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    platform, platform_options = (
        get_platform_options(url)
    )

    print(
        f"IRISSAVE PLATFORM: {platform}",
        flush=True,
    )

    if platform == "TIKTOK":
        try:
            print(
                "TIKTOK DIRECT: пробую прямой способ",
                flush=True,
            )

            direct_path = _download_tiktok_direct(
                url=url,
                folder_path=folder_path,
                progress_hook=progress_hook,
            )

            return direct_path

        except Exception as direct_error:
            print(
                "TIKTOK DIRECT: не сработал — "
                f"{type(direct_error).__name__}: "
                f"{direct_error}",
                flush=True,
            )

            print(
                "TIKTOK FALLBACK: запускаю yt-dlp",
                flush=True,
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
        "http_headers": BROWSER_HEADERS,
    }

    options.update(
        platform_options
    )

    try:
        with yt_dlp.YoutubeDL(
            options
        ) as downloader:
            info = downloader.extract_info(
                url,
                download=True,
            )

            print(
                "\n========== FILES AFTER YT-DLP ==========",
                flush=True,
            )

            for file_path in sorted(
                folder_path.iterdir()
            ):
                if file_path.is_file():
                    print(
                        f"FILE: {file_path.name} | "
                        f"SIZE: "
                        f"{file_path.stat().st_size} bytes",
                        flush=True,
                    )

            print(
                "========================================\n",
                flush=True,
            )

            if not isinstance(info, dict):
                raise RuntimeError(
                    "yt-dlp не вернул информацию о видео"
                )

            prepared_path = Path(
                downloader.prepare_filename(info)
            )

    except yt_dlp.utils.DownloadError as error:
        if platform == "TIKTOK":
            raise RuntimeError(
                "TikTok временно не отдал видео "
                "ни прямым способом, ни через yt-dlp"
            ) from error

        raise RuntimeError(
            str(error)
        ) from error

    video_path = _find_downloaded_video(
        folder_path=folder_path,
        prepared_path=prepared_path,
        files_before=files_before,
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
            "Диагностический файл больше 48 МБ"
        )

    return video_path
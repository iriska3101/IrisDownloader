import html
import os
import re
from pathlib import Path
from typing import Any, Callable

import httpx
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
    platform: str,
    info: dict[str, Any],
    video_path: Path,
) -> None:
    """
    Выводит в Render Logs информацию
    о скачанном файле.
    """
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
    """
    Ищет прямые URL видео в JSON TikTok.
    Сначала собирает playAddr/playUrl, затем downloadAddr.
    """
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
    """
    Запасной поиск прямых URL, если TikTok поменял
    структуру JSON, но оставил адрес видео в HTML.
    """
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


def _download_tiktok_direct(
    url: str,
    folder_path: Path,
    progress_hook: Callable[
        [dict[str, Any]],
        None,
    ],
) -> Path:
    """
    Для TikTok сначала пытается получить прямой видеофайл
    из данных страницы без yt-dlp.
    """
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

    headers = {
        **BROWSER_HEADERS,
        "Referer": final_url,
    }

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
            try:
                with client.stream(
                    "GET",
                    video_url,
                ) as media_response:
                    media_response.raise_for_status()

                    total = int(
                        media_response.headers.get(
                            "content-length",
                            "0",
                        )
                        or 0
                    )

                    path = (
                        folder_path
                        / f"tiktok-direct-{index}.mp4"
                    )

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

                            progress_hook(
                                {
                                    "status": "downloading",
                                    "downloaded_bytes": downloaded,
                                    "total_bytes": total or None,
                                    "filename": str(path),
                                }
                            )

                    if (
                        path.exists()
                        and path.stat().st_size > 100_000
                    ):
                        progress_hook(
                            {
                                "status": "finished",
                                "downloaded_bytes": (
                                    path.stat().st_size
                                ),
                                "total_bytes": (
                                    path.stat().st_size
                                ),
                                "filename": str(path),
                            }
                        )

                        print(
                            "TIKTOK DIRECT: "
                            f"скачан {path.name}, "
                            f"{path.stat().st_size} bytes",
                            flush=True,
                        )

                        return path

                    path.unlink(missing_ok=True)

            except (
                httpx.HTTPError,
                OSError,
            ) as error:
                last_error = error
                continue

    raise RuntimeError(
        "TikTok нашёл адрес видео, "
        "но не разрешил скачать файл"
    ) from last_error


def download_video_with_progress(
    url: str,
    folder: str,
    progress_hook: Callable[
        [dict[str, Any]],
        None,
    ],
) -> Path:
    """
    Загружает видео с отдельными настройками
    для TikTok, Instagram и YouTube.

    TikTok:
    1. сначала прямой файл из JSON/HTML страницы;
    2. если это не сработало — fallback на yt-dlp.
    """
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

            if (
                direct_path.stat().st_size
                > TELEGRAM_SAFE_SIZE
            ):
                raise RuntimeError(
                    "Видео TikTok больше 48 МБ"
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
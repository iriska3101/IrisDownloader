from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import yt_dlp

from config import BROWSER_HEADERS


_IMAGE_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
    "webp",
}

_VIDEO_EXTENSIONS = {
    "mp4",
    "mov",
    "m4v",
    "webm",
}


def _clean_instagram_url(url: str) -> str:
    """
    Убирает img_index, igsh и остальные параметры.

    Instagram должен вернуть всю публикацию,
    а не только выбранный элемент карусели.
    """
    parts = urlsplit(url)

    return urlunsplit(
        (
            parts.scheme or "https",
            parts.netloc,
            parts.path,
            "",
            "",
        )
    )


def _is_http_url(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(("http://", "https://"))
    )


def _get_extension_from_url(url: str) -> str:
    path = urlsplit(url).path.lower()

    suffix = Path(path).suffix.lower().lstrip(".")

    return suffix


def _choose_best_thumbnail(
    entry: dict[str, Any],
) -> str | None:
    """
    Выбирает изображение максимального разрешения.
    """
    candidates: list[
        tuple[int, str]
    ] = []

    thumbnails = entry.get("thumbnails")

    if isinstance(thumbnails, list):
        for thumbnail in thumbnails:
            if not isinstance(thumbnail, dict):
                continue

            thumbnail_url = thumbnail.get("url")

            if not _is_http_url(thumbnail_url):
                continue

            width = thumbnail.get("width")
            height = thumbnail.get("height")

            try:
                area = int(width or 0) * int(
                    height or 0
                )
            except (TypeError, ValueError):
                area = 0

            candidates.append(
                (
                    area,
                    thumbnail_url,
                )
            )

    direct_thumbnail = entry.get("thumbnail")

    if _is_http_url(direct_thumbnail):
        candidates.append(
            (
                0,
                direct_thumbnail,
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return candidates[0][1]


def _choose_best_video_url(
    entry: dict[str, Any],
) -> str | None:
    """
    Выбирает наиболее совместимый MP4-вариант.

    H.264 предпочитается перед VP9/AV1,
    потому что он надёжнее воспроизводится
    в Telegram и на iPhone.
    """
    candidates: list[
        tuple[int, int, str]
    ] = []

    formats = entry.get("formats")

    if isinstance(formats, list):
        for media_format in formats:
            if not isinstance(
                media_format,
                dict,
            ):
                continue

            media_url = media_format.get("url")

            if not _is_http_url(media_url):
                continue

            extension = str(
                media_format.get("ext") or ""
            ).lower()

            video_codec = str(
                media_format.get("vcodec") or ""
            ).lower()

            if video_codec in {
                "",
                "none",
            }:
                continue

            compatibility_score = 0

            if extension == "mp4":
                compatibility_score += 100

            if video_codec.startswith(
                (
                    "avc1",
                    "h264",
                )
            ):
                compatibility_score += 200

            if video_codec.startswith(
                (
                    "vp9",
                    "vp09",
                    "av01",
                )
            ):
                compatibility_score -= 100

            width = media_format.get("width")
            height = media_format.get("height")

            try:
                area = int(width or 0) * int(
                    height or 0
                )
            except (TypeError, ValueError):
                area = 0

            candidates.append(
                (
                    compatibility_score,
                    area,
                    media_url,
                )
            )

    if candidates:
        candidates.sort(
            key=lambda item: (
                item[0],
                item[1],
            ),
            reverse=True,
        )

        return candidates[0][2]

    direct_url = entry.get("url")

    if _is_http_url(direct_url):
        extension = str(
            entry.get("ext") or ""
        ).lower()

        url_extension = _get_extension_from_url(
            direct_url
        )

        if (
            extension in _VIDEO_EXTENSIONS
            or url_extension in _VIDEO_EXTENSIONS
        ):
            return direct_url

    return None


def _looks_like_video(
    entry: dict[str, Any],
) -> bool:
    extension = str(
        entry.get("ext") or ""
    ).lower()

    video_codec = str(
        entry.get("vcodec") or ""
    ).lower()

    media_type = str(
        entry.get("_type") or ""
    ).lower()

    if extension in _VIDEO_EXTENSIONS:
        return True

    if video_codec not in {
        "",
        "none",
    }:
        return True

    if media_type == "video":
        return True

    formats = entry.get("formats")

    if isinstance(formats, list):
        for media_format in formats:
            if not isinstance(
                media_format,
                dict,
            ):
                continue

            codec = str(
                media_format.get("vcodec") or ""
            ).lower()

            if codec not in {
                "",
                "none",
            }:
                return True

    return False


def _collect_instagram_media(
    info: dict[str, Any],
) -> list[tuple[str, str]]:
    """
    Возвращает элементы публикации в исходном порядке.

    Результат:
    [
        ("image", "https://..."),
        ("video", "https://..."),
    ]
    """
    result: list[
        tuple[str, str]
    ] = []

    seen_urls: set[str] = set()

    def add_item(
        media_type: str,
        media_url: str | None,
    ) -> None:
        if not media_url:
            return

        if media_url in seen_urls:
            return

        seen_urls.add(media_url)

        result.append(
            (
                media_type,
                media_url,
            )
        )

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)

            return

        if not isinstance(value, dict):
            return

        entries = value.get("entries")

        if isinstance(entries, list):
            for entry in entries:
                walk(entry)

            return

        if _looks_like_video(value):
            video_url = _choose_best_video_url(
                value
            )

            if video_url:
                add_item(
                    "video",
                    video_url,
                )
                return

        image_url = _choose_best_thumbnail(
            value
        )

        if image_url:
            add_item(
                "image",
                image_url,
            )

    walk(info)

    return result


def _extension_from_content_type(
    content_type: str,
    media_type: str,
) -> str:
    lowered = content_type.lower()

    if media_type == "video":
        if "webm" in lowered:
            return ".webm"

        if "quicktime" in lowered:
            return ".mov"

        return ".mp4"

    if "png" in lowered:
        return ".png"

    if "webp" in lowered:
        return ".webp"

    return ".jpg"


def download_instagram_media(
    url: str,
    folder: str,
) -> list[Path]:
    """
    Загружает все элементы Instagram-публикации:

    - одиночное фото;
    - одиночное видео;
    - фотокарусель;
    - карусель из видео;
    - смешанную карусель.
    """
    clean_url = _clean_instagram_url(url)

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "socket_timeout": 60,
        "retries": 2,
        "extractor_retries": 2,
    }

    try:
        with yt_dlp.YoutubeDL(
            options
        ) as downloader:
            info = downloader.extract_info(
                clean_url,
                download=False,
                process=False,
            )

    except yt_dlp.utils.DownloadError as error:
        raise RuntimeError(
            "Instagram не отдал структуру "
            f"публикации: {error}"
        ) from error

    if not isinstance(info, dict):
        raise RuntimeError(
            "Instagram не вернул данные публикации."
        )

    media_items = _collect_instagram_media(
        info
    )

    if not media_items:
        raise RuntimeError(
            "Instagram не отдал доступные "
            "фото или видео."
        )

    output_folder = Path(folder)

    output_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    headers = {
        **BROWSER_HEADERS,
        "Referer": clean_url,
    }

    downloaded: list[Path] = []

    timeout = httpx.Timeout(
        connect=30,
        read=90,
        write=30,
        pool=30,
    )

    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=timeout,
    ) as client:
        for index, (
            media_type,
            media_url,
        ) in enumerate(
            media_items,
            start=1,
        ):
            try:
                response = client.get(
                    media_url
                )

                response.raise_for_status()

            except httpx.HTTPError as error:
                print(
                    "Instagram media download "
                    f"failed #{index}: {error}",
                    flush=True,
                )
                continue

            content_type = response.headers.get(
                "content-type",
                "",
            ).lower()

            if media_type == "image":
                if (
                    content_type
                    and not content_type.startswith(
                        "image/"
                    )
                ):
                    continue

            else:
                if (
                    content_type
                    and not (
                        content_type.startswith(
                            "video/"
                        )
                        or "octet-stream"
                        in content_type
                    )
                ):
                    continue

            extension = (
                _extension_from_content_type(
                    content_type,
                    media_type,
                )
            )

            path = output_folder / (
                "instagram_"
                f"{index:02d}"
                f"{extension}"
            )

            path.write_bytes(
                response.content
            )

            minimum_size = (
                5_000
                if media_type == "image"
                else 20_000
            )

            if path.stat().st_size < minimum_size:
                path.unlink(
                    missing_ok=True
                )
                continue

            downloaded.append(path)

    if not downloaded:
        raise RuntimeError(
            "Instagram не разрешил скачать "
            "элементы публикации."
        )

    return downloaded
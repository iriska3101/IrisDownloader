import html
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import imageio_ffmpeg
import yt_dlp

from config import BROWSER_HEADERS
from utils.helpers import clean_text


@dataclass
class AudioMetadata:
    title: str = "TikTok audio"
    performer: str = "Неизвестен"
    cover_url: str | None = None
    referer: str | None = None


def extract_json_objects(page_html: str) -> list[Any]:
    objects: list[Any] = []

    patterns = [
        (
            r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"'
            r"[^>]*>(.*?)</script>"
        ),
        r'<script[^>]+id="SIGI_STATE"[^>]*>(.*?)</script>',
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    ]

    for pattern in patterns:
        matches = re.findall(
            pattern,
            page_html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        for match in matches:
            try:
                objects.append(
                    json.loads(html.unescape(match.strip()))
                )
            except json.JSONDecodeError:
                continue

    return objects


def urls_from_value(value: Any) -> list[str]:
    found: list[str] = []

    if isinstance(value, str):
        if value.startswith("http"):
            found.append(value)

    elif isinstance(value, list):
        for item in value:
            found.extend(urls_from_value(item))

    elif isinstance(value, dict):
        for key in (
            "urlList",
            "url_list",
            "url",
            "uri",
            "src",
        ):
            if key in value:
                found.extend(urls_from_value(value[key]))

    return found


def first_url(value: Any) -> str | None:
    urls = urls_from_value(value)
    return urls[0] if urls else None


def find_photo_urls(data: Any) -> list[str]:
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            image_post = value.get("imagePost")

            if isinstance(image_post, dict):
                images = image_post.get("images", [])

                if isinstance(images, list):
                    for image in images:
                        if not isinstance(image, dict):
                            continue

                        image_url = (
                            image.get("imageURL")
                            or image.get("imageUrl")
                            or image.get("image_url")
                        )

                        image_link = first_url(image_url)

                        if image_link:
                            found.append(image_link)

            for child in value.values():
                walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)

    return list(dict.fromkeys(found))


def looks_like_audio_url(url: str) -> bool:
    lowered = url.lower()

    return not any(
        blocked in lowered
        for blocked in (
            "avatar",
            "image",
            "cover",
            "thumbnail",
            "profile",
        )
    )


def extract_music_from_dict(
    music: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None]:
    title = None
    performer = None
    music_url = None
    cover_url = None

    for key in (
        "title",
        "musicName",
        "music_name",
        "songName",
        "song_name",
    ):
        if isinstance(music.get(key), str):
            title = music[key]
            break

    for key in (
        "authorName",
        "author_name",
        "artist",
        "artistName",
        "artist_name",
        "ownerName",
        "owner_name",
    ):
        if isinstance(music.get(key), str):
            performer = music[key]
            break

    for key in (
        "playUrl",
        "playURL",
        "play_url",
        "playUri",
        "play_uri",
        "musicPlayUrl",
        "music_play_url",
    ):
        candidate = first_url(music.get(key))

        if candidate and looks_like_audio_url(candidate):
            music_url = candidate
            break

    for key in (
        "coverLarge",
        "cover_large",
        "coverMedium",
        "cover_medium",
        "coverThumb",
        "cover_thumb",
        "cover",
        "albumCover",
        "album_cover",
    ):
        candidate = first_url(music.get(key))

        if candidate:
            cover_url = candidate
            break

    return title, performer, music_url, cover_url


def find_music_metadata(
    data: Any,
) -> tuple[str | None, str | None, str | None, str | None]:
    title = None
    performer = None
    music_url = None
    cover_url = None

    music_names = {
        "music",
        "musicinfo",
        "musicdetail",
    }

    def normalize(key: str) -> str:
        return (
            key.replace("-", "")
            .replace("_", "")
            .lower()
        )

    def walk(value: Any) -> None:
        nonlocal title
        nonlocal performer
        nonlocal music_url
        nonlocal cover_url

        if isinstance(value, dict):
            for key, child in value.items():
                if (
                    normalize(str(key)) in music_names
                    and isinstance(child, dict)
                ):
                    (
                        found_title,
                        found_performer,
                        found_music_url,
                        found_cover_url,
                    ) = extract_music_from_dict(child)

                    title = title or found_title
                    performer = performer or found_performer
                    music_url = music_url or found_music_url
                    cover_url = cover_url or found_cover_url

                walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)

    return title, performer, music_url, cover_url


def find_post_author(data: Any) -> str | None:
    found: list[str] = []

    def walk(
        value: Any,
        inside_author: bool = False,
    ) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = (
                    str(key)
                    .replace("-", "_")
                    .lower()
                )

                now_inside_author = (
                    inside_author
                    or normalized
                    in {
                        "author",
                        "authorinfo",
                        "author_info",
                    }
                )

                if (
                    now_inside_author
                    and normalized
                    in {
                        "uniqueid",
                        "unique_id",
                        "nickname",
                        "username",
                    }
                    and isinstance(child, str)
                    and child.strip()
                ):
                    found.append(child.strip())

                walk(
                    child,
                    now_inside_author,
                )

        elif isinstance(value, list):
            for child in value:
                walk(
                    child,
                    inside_author,
                )

    walk(data)

    return found[0] if found else None


def extract_fallback_photo_urls(
    page_html: str,
) -> list[str]:
    decoded = html.unescape(page_html)
    decoded = decoded.replace("\\u002F", "/")
    decoded = decoded.replace("\\/", "/")

    candidates = re.findall(
        r'https?://[^"\'\s<>]+',
        decoded,
    )

    found: list[str] = []

    for candidate in candidates:
        cleaned = candidate.rstrip("\\,}]")
        lowered = cleaned.lower()

        looks_like_image = (
            "tiktokcdn" in lowered
            or "byteimg" in lowered
            or "ibytedtos" in lowered
        )

        unwanted = any(
            blocked in lowered
            for blocked in (
                "avatar",
                "profile",
                "music",
            )
        )

        if looks_like_image and not unwanted:
            found.append(cleaned)

    return list(dict.fromkeys(found))


def get_tiktok_post_assets(
    url: str,
) -> tuple[
    list[str],
    str | None,
    str,
    AudioMetadata,
]:
    with httpx.Client(
        headers=BROWSER_HEADERS,
        follow_redirects=True,
        timeout=httpx.Timeout(45),
    ) as client:
        response = client.get(url)
        response.raise_for_status()

        final_url = str(response.url)
        page_html = response.text

    photo_urls: list[str] = []
    music_url = None
    title = None
    performer = None
    cover_url = None
    post_author = None

    for data in extract_json_objects(page_html):
        photo_urls.extend(
            find_photo_urls(data)
        )

        (
            found_title,
            found_performer,
            found_music_url,
            found_cover_url,
        ) = find_music_metadata(data)

        title = title or found_title
        performer = performer or found_performer
        music_url = music_url or found_music_url
        cover_url = cover_url or found_cover_url
        post_author = (
            post_author
            or find_post_author(data)
        )

    photo_urls = list(
        dict.fromkeys(photo_urls)
    )

    if not photo_urls:
        photo_urls = (
            extract_fallback_photo_urls(
                page_html
            )
        )

    if not cover_url and photo_urls:
        cover_url = photo_urls[0]

    if not performer and post_author:
        performer = (
            post_author
            if post_author.startswith("@")
            else f"@{post_author}"
        )

    metadata = AudioMetadata(
        title=clean_text(
            title,
            "TikTok audio",
        ),
        performer=clean_text(
            performer,
            "Неизвестен",
        ),
        cover_url=cover_url,
        referer=final_url,
    )

    return (
        photo_urls,
        music_url,
        final_url,
        metadata,
    )


def metadata_from_yt_dlp(
    info: dict[str, Any],
) -> AudioMetadata:
    title = (
        info.get("track")
        or info.get("title")
        or info.get("fulltitle")
        or "TikTok audio"
    )

    performer = (
        info.get("artist")
        or info.get("creator")
        or info.get("uploader")
        or info.get("channel")
        or info.get("uploader_id")
        or "Неизвестен"
    )

    cover_url = info.get("thumbnail")

    if not cover_url:
        thumbnails = info.get("thumbnails")

        if isinstance(thumbnails, list):
            for thumbnail in reversed(
                thumbnails
            ):
                if not isinstance(
                    thumbnail,
                    dict,
                ):
                    continue

                candidate = thumbnail.get(
                    "url"
                )

                if isinstance(
                    candidate,
                    str,
                ):
                    cover_url = candidate
                    break

    return AudioMetadata(
        title=clean_text(
            title,
            "TikTok audio",
        ),
        performer=clean_text(
            performer,
            "Неизвестен",
        ),
        cover_url=(
            cover_url
            if isinstance(cover_url, str)
            else None
        ),
    )


def find_downloaded_video(
    folder: str,
    prepared_path: Path,
    files_before: set[Path],
) -> Path:
    folder_path = Path(folder)

    direct_candidates = [
        prepared_path.with_suffix(".mp4"),
        prepared_path,
    ]

    for candidate in direct_candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    allowed_extensions = {
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".m4v",
    }

    new_files = [
        file
        for file in folder_path.iterdir()
        if (
            file.is_file()
            and file.resolve() not in files_before
            and file.suffix.lower() in allowed_extensions
            and file.stat().st_size > 0
        )
    ]

    if not new_files:
        raise FileNotFoundError(
            "Итоговый видеофайл после загрузки не найден"
        )

    mp4_files = [
        file
        for file in new_files
        if file.suffix.lower() == ".mp4"
    ]

    candidates = mp4_files or new_files

    return max(
        candidates,
        key=lambda item: (
            item.stat().st_mtime,
            item.stat().st_size,
        ),
    )


def download_video(
    url: str,
    folder: str,
) -> Path:
    folder_path = Path(folder)
    folder_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    template = os.path.join(
        folder,
        "%(title).80s-%(id)s.%(ext)s",
    )

    files_before = {
        file.resolve()
        for file in folder_path.iterdir()
        if file.is_file()
    }

    options: dict[str, Any] = {
        "outtmpl": template,
        "format": (
            "bestvideo*[height<=1080][vcodec^=avc1]"
            "+bestaudio[ext=m4a]/"
            "bestvideo*[height<=1080]+bestaudio/"
            "best[height<=1080][ext=mp4][acodec!=none]/"
            "best[height<=1080][acodec!=none]/"
            "best"
        ),
        "merge_output_format": "mp4",
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
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
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(
            url,
            download=True,
        )

        if not isinstance(info, dict):
            raise RuntimeError(
                "Сервис не вернул данные о видео"
            )

        prepared_path = Path(
            downloader.prepare_filename(info)
        )

    return find_downloaded_video(
        folder=folder,
        prepared_path=prepared_path,
        files_before=files_before,
    )


def download_audio_source(
    url: str,
    folder: str,
) -> tuple[Path, AudioMetadata]:
    """
    Скачивает источник с реальной аудиодорожкой.

    Для TikTok сначала выбирается готовый MP4
    с H.264 и настоящим звуком.
    """
    folder_path = Path(folder)

    folder_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    template = os.path.join(
        folder,
        "source-%(id)s.%(ext)s",
    )

    files_before = {
        file.resolve()
        for file in folder_path.iterdir()
        if file.is_file()
    }

    options: dict[str, Any] = {
        "outtmpl": template,

        "format": (
            "best[ext=mp4]"
            "[vcodec~='^(avc1|h264)']"
            "[acodec!=none]/"
            "best[ext=mp4]"
            "[acodec!=none]/"
            "bestaudio[acodec!=none]/"
            "best[acodec!=none]"
        ),

        "format_sort": [
            "vcodec:h264",
            "hasaud",
            "res",
            "fps",
        ],

        "merge_output_format": "mp4",
        "ffmpeg_location": (
            imageio_ffmpeg.get_ffmpeg_exe()
        ),

        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,

        "socket_timeout": 120,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,

        "continuedl": True,
        "overwrites": True,
    }

    with yt_dlp.YoutubeDL(
        options
    ) as downloader:
        info = downloader.extract_info(
            url,
            download=True,
        )

        if not isinstance(info, dict):
            raise RuntimeError(
                "Сервис не вернул данные об аудио"
            )

        prepared_path = Path(
            downloader.prepare_filename(info)
        )

    metadata = metadata_from_yt_dlp(info)

    direct_candidates = [
        prepared_path.with_suffix(".mp4"),
        prepared_path,
    ]

    for candidate in direct_candidates:
        if (
            candidate.exists()
            and candidate.is_file()
            and candidate.stat().st_size > 0
        ):
            return candidate, metadata

    allowed_extensions = {
        ".m4a",
        ".mp3",
        ".aac",
        ".ogg",
        ".opus",
        ".webm",
        ".mp4",
        ".mov",
        ".mkv",
    }

    new_files = [
        file
        for file in folder_path.iterdir()
        if (
            file.is_file()
            and file.resolve() not in files_before
            and file.suffix.lower()
            in allowed_extensions
            and file.stat().st_size > 0
        )
    ]

    if not new_files:
        raise FileNotFoundError(
            "Файл с аудиодорожкой не найден"
        )

    source_path = max(
        new_files,
        key=lambda item: (
            item.stat().st_mtime,
            item.stat().st_size,
        ),
    )

    return source_path, metadata


def download_direct_music(
    music_url: str,
    final_url: str,
    folder: str,
) -> Path:
    headers = {
        **BROWSER_HEADERS,
        "Referer": final_url,
    }

    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(60),
    ) as client:
        response = client.get(music_url)
        response.raise_for_status()

    content_type = response.headers.get(
        "content-type",
        "",
    ).lower()

    if "mpeg" in content_type:
        extension = ".mp3"
    elif "ogg" in content_type:
        extension = ".ogg"
    elif "webm" in content_type:
        extension = ".webm"
    else:
        extension = ".m4a"

    path = Path(folder) / f"photo_music{extension}"
    path.write_bytes(response.content)

    if path.stat().st_size < 5_000:
        raise RuntimeError(
            "TikTok отдал слишком маленький музыкальный файл"
        )

    return path


def download_thumbnail(
    metadata: AudioMetadata,
    folder: str,
) -> Path | None:
    if not metadata.cover_url:
        return None

    headers = dict(BROWSER_HEADERS)

    if metadata.referer:
        headers["Referer"] = metadata.referer

    raw_cover = Path(folder) / "cover_source"

    try:
        with httpx.Client(
            headers=headers,
            follow_redirects=True,
            timeout=httpx.Timeout(45),
        ) as client:
            response = client.get(metadata.cover_url)
            response.raise_for_status()

        if len(response.content) < 5_000:
            return None

        raw_cover.write_bytes(response.content)

    except httpx.HTTPError:
        return None

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    output = Path(folder) / "cover.jpg"

    for quality in (5, 9, 13, 17, 21, 25):
        result = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(raw_cover),
                "-vf",
                (
                    "scale=320:320:"
                    "force_original_aspect_ratio=decrease,"
                    "pad=320:320:(ow-iw)/2:(oh-ih)/2"
                ),
                "-frames:v",
                "1",
                "-q:v",
                str(quality),
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )

        if (
            result.returncode == 0
            and output.exists()
            and output.stat().st_size <= 190_000
        ):
            return output

    return None


def convert_to_mp3(
    source: Path,
    folder: str,
    metadata: AudioMetadata,
    cover: Path | None,
) -> Path:
    output = Path(folder) / "IriSSave_audio.mp3"
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    base = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
    ]

    if cover:
        command = [
            *base,
            "-i",
            str(cover),
            "-map",
            "0:a:0",
            "-map",
            "1:v:0",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "192k",
            "-codec:v",
            "mjpeg",
            "-id3v2_version",
            "3",
            "-metadata:s:v",
            "title=Album cover",
            "-metadata:s:v",
            "comment=Cover (front)",
            "-metadata",
            f"title={metadata.title}",
            "-metadata",
            f"artist={metadata.performer}",
            str(output),
        ]

    else:
        command = [
            *base,
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "192k",
            "-id3v2_version",
            "3",
            "-metadata",
            f"title={metadata.title}",
            "-metadata",
            f"artist={metadata.performer}",
            str(output),
        ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    if (
        result.returncode == 0
        and output.exists()
        and output.stat().st_size > 0
    ):
        return output

    fallback = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "192k",
            "-id3v2_version",
            "3",
            "-metadata",
            f"title={metadata.title}",
            "-metadata",
            f"artist={metadata.performer}",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    if (
        fallback.returncode != 0
        or not output.exists()
        or output.stat().st_size == 0
    ):
        error = (
            fallback.stderr.strip()
            or result.stderr.strip()
            or "FFmpeg не смог создать MP3"
        )

        raise RuntimeError(
            error[-2000:]
        )

    return output


def download_audio_as_mp3(
    url: str,
    folder: str,
) -> tuple[
    Path,
    AudioMetadata,
    Path | None,
]:
    """
    Загружает звуковую дорожку и создаёт MP3.

    Если обычное извлечение TikTok-аудио не удалось,
    пробует прямую музыкальную ссылку.
    """
    source: Path
    metadata: AudioMetadata

    try:
        source, metadata = download_audio_source(
            url,
            folder,
        )

        cover = download_thumbnail(
            metadata,
            folder,
        )

        try:
            mp3 = convert_to_mp3(
                source,
                folder,
                metadata,
                cover,
            )

            return mp3, metadata, cover

        except Exception as conversion_error:
            if "tiktok.com" not in url.lower():
                raise RuntimeError(
                    "Не удалось преобразовать "
                    "звуковую дорожку в MP3.\n"
                    f"Причина: {conversion_error}"
                ) from conversion_error

            normal_error: Exception = conversion_error

    except Exception as download_error:
        if "tiktok.com" not in url.lower():
            raise RuntimeError(
                "Не удалось получить звуковую дорожку "
                "из этой публикации.\n"
                f"Причина: {download_error}"
            ) from download_error

        normal_error = download_error

    try:
        (
            _,
            music_url,
            final_url,
            fallback_metadata,
        ) = get_tiktok_post_assets(url)

        if not music_url:
            raise RuntimeError(
                "TikTok не предоставил прямую "
                "ссылку на музыку"
            )

        source = download_direct_music(
            music_url,
            final_url,
            folder,
        )

        metadata = fallback_metadata

        cover = download_thumbnail(
            metadata,
            folder,
        )

        mp3 = convert_to_mp3(
            source,
            folder,
            metadata,
            cover,
        )

        return mp3, metadata, cover

    except Exception as fallback_error:
        raise RuntimeError(
            "Не удалось получить MP3 из публикации.\n"
            f"Основная попытка: {normal_error}\n"
            f"Резервная попытка: {fallback_error}"
        ) from fallback_error


def download_photos(
    url: str,
    folder: str,
) -> list[Path]:
    """
    Выбирает загрузчик фотографий
    в зависимости от платформы.
    """
    from services.photo_downloaders.common import (
        download_photos as download_platform_photos,
    )

    return download_platform_photos(
        url,
        folder,
    )

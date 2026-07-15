import asyncio
import html
import json
import os
import re
import subprocess
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx
import imageio_ffmpeg
import yt_dlp
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


BOT_TOKEN = os.environ["BOT_TOKEN"]
PORT = int(os.environ.get("PORT", "10000"))
RENDER_EXTERNAL_URL = os.environ["RENDER_EXTERNAL_URL"]

WEBHOOK_PATH = "telegram"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}/{WEBHOOK_PATH}"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}


@dataclass
class AudioMetadata:
    title: str = "TikTok audio"
    performer: str = "Неизвестен"
    cover_url: str | None = None
    referer: str | None = None


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None:
        return

    await update.message.reply_text(
        "Привет! 👋\n\n"
        "Отправь мне ссылку, а затем выбери:\n"
        "🎬 скачать видео\n"
        "🎵 скачать звук в MP3\n"
        "🖼 скачать фотографии"
    )


def find_link(text: str) -> str | None:
    match = re.search(r"https?://\S+", text)

    if not match:
        return None

    return match.group(0).rstrip(".,)")


def clean_text(
    value: Any,
    fallback: str,
    max_length: int = 64,
) -> str:
    if not isinstance(value, str):
        return fallback

    cleaned = re.sub(r"\s+", " ", value).strip()

    if not cleaned:
        return fallback

    return cleaned[:max_length]


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

    def walk(value: Any, inside_author: bool = False) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).replace("-", "_").lower()

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

                walk(child, now_inside_author)

        elif isinstance(value, list):
            for child in value:
                walk(child, inside_author)

    walk(data)

    return found[0] if found else None


def extract_fallback_photo_urls(page_html: str) -> list[str]:
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
) -> tuple[list[str], str | None, str, AudioMetadata]:
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
        photo_urls.extend(find_photo_urls(data))

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
        post_author = post_author or find_post_author(data)

    photo_urls = list(dict.fromkeys(photo_urls))

    if not photo_urls:
        photo_urls = extract_fallback_photo_urls(page_html)

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

    return photo_urls, music_url, final_url, metadata


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
            for thumbnail in reversed(thumbnails):
                if isinstance(thumbnail, dict):
                    candidate = thumbnail.get("url")

                    if isinstance(candidate, str):
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


def download_video(
    url: str,
    folder: str,
) -> Path:
    template = os.path.join(
        folder,
        "%(title).80s-%(id)s.%(ext)s",
    )

    options: dict[str, Any] = {
        "outtmpl": template,
        "format": "best[ext=mp4]/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "socket_timeout": 45,
        "retries": 2,
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(
            url,
            download=True,
        )
        downloaded_path = downloader.prepare_filename(info)

    path = Path(downloaded_path)

    if not path.exists():
        files = [
            file
            for file in Path(folder).iterdir()
            if file.is_file()
        ]

        if files:
            path = max(
                files,
                key=lambda item: item.stat().st_size,
            )

    return path


def download_audio_source(
    url: str,
    folder: str,
) -> tuple[Path, AudioMetadata]:
    template = os.path.join(
        folder,
        "source-%(id)s.%(ext)s",
    )

    options: dict[str, Any] = {
        "outtmpl": template,
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "socket_timeout": 45,
        "retries": 2,
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(
            url,
            download=True,
        )
        downloaded_path = downloader.prepare_filename(info)

    metadata = metadata_from_yt_dlp(info)
    path = Path(downloaded_path)

    if path.exists():
        return path, metadata

    candidates = [
        file
        for file in Path(folder).iterdir()
        if file.is_file()
        and file.suffix.lower() != ".mp3"
    ]

    if not candidates:
        raise FileNotFoundError(
            "Исходная аудиодорожка не найдена"
        )

    return (
        max(
            candidates,
            key=lambda item: item.stat().st_size,
        ),
        metadata,
    )


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
    output = Path(folder) / "IrisDownloader_audio.mp3"
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

    if result.returncode == 0 and output.exists():
        return output

    fallback = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
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

    if fallback.returncode != 0 or not output.exists():
        error = (
            fallback.stderr.strip()
            or result.stderr.strip()
            or "FFmpeg не смог создать MP3"
        )

        raise RuntimeError(error[-2000:])

    return output


def download_audio_as_mp3(
    url: str,
    folder: str,
) -> tuple[Path, AudioMetadata, Path | None]:
    try:
        source, metadata = download_audio_source(
            url,
            folder,
        )

    except Exception as normal_error:
        (
            _,
            music_url,
            final_url,
            metadata,
        ) = get_tiktok_post_assets(url)

        if not music_url:
            raise RuntimeError(
                "Не удалось найти музыку в фотопубликации.\n"
                f"Обычная загрузка тоже не сработала: "
                f"{normal_error}"
            ) from normal_error

        source = download_direct_music(
            music_url,
            final_url,
            folder,
        )

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


def download_photos(
    url: str,
    folder: str,
) -> list[Path]:
    (
        photo_urls,
        _,
        final_url,
        _,
    ) = get_tiktok_post_assets(url)

    if not photo_urls:
        raise RuntimeError(
            "TikTok не отдал список фотографий."
        )

    headers = {
        **BROWSER_HEADERS,
        "Referer": final_url,
    }

    downloaded: list[Path] = []

    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(60),
    ) as client:
        for index, photo_url in enumerate(
            photo_urls,
            start=1,
        ):
            try:
                response = client.get(photo_url)
                response.raise_for_status()

                content_type = response.headers.get(
                    "content-type",
                    "",
                ).lower()

                if "png" in content_type:
                    extension = ".png"
                elif "webp" in content_type:
                    extension = ".webp"
                else:
                    extension = ".jpg"

                path = Path(folder) / (
                    f"tiktok_photo_{index:02d}{extension}"
                )

                path.write_bytes(response.content)

                if path.stat().st_size < 5_000:
                    path.unlink(missing_ok=True)
                    continue

                downloaded.append(path)

            except httpx.HTTPError:
                continue

    if not downloaded:
        raise RuntimeError(
            "TikTok не разрешил скачать фотографии."
        )

    return downloaded


def is_temporary_error(error: Exception) -> bool:
    text = str(error).lower()

    temporary_words = (
        "timed out",
        "timeout",
        "temporarily",
        "connection reset",
        "connection error",
        "network",
        "remote end closed",
        "server disconnected",
        "502",
        "503",
        "504",
    )

    return (
        isinstance(
            error,
            (
                TimeoutError,
                TimedOut,
                NetworkError,
                httpx.TimeoutException,
                httpx.NetworkError,
            ),
        )
        or any(word in text for word in temporary_words)
    )


async def run_with_retry(
    operation: Callable[..., Any],
    *args: Any,
    status_message,
) -> Any:
    try:
        return await asyncio.to_thread(
            operation,
            *args,
        )

    except Exception as first_error:
        if not is_temporary_error(first_error):
            raise

        await status_message.edit_text(
            "Первая попытка не удалась.\n"
            "Повторяю ещё раз… 🔄"
        )

        await asyncio.sleep(3)

        return await asyncio.to_thread(
            operation,
            *args,
        )


async def handle_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None:
        return

    url = find_link(update.message.text or "")

    if not url:
        await update.message.reply_text(
            "Пришли мне ссылку 🔗"
        )
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎬 Видео",
                    callback_data="download_video",
                ),
                InlineKeyboardButton(
                    "🎵 MP3",
                    callback_data="download_audio",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🖼 Фотографии",
                    callback_data="download_photos",
                )
            ],
        ]
    )

    message = await update.message.reply_text(
        "Что скачать?",
        reply_markup=keyboard,
    )

    context.user_data[
        f"url_{message.message_id}"
    ] = url


async def send_photo_albums(
    message,
    photo_paths: list[Path],
) -> None:
    for start_index in range(
        0,
        len(photo_paths),
        10,
    ):
        chunk = photo_paths[
            start_index : start_index + 10
        ]

        if len(chunk) == 1:
            with chunk[0].open("rb") as photo:
                await message.reply_photo(
                    photo=photo,
                    caption=(
                        f"Готово 🖼\n"
                        f"Фотографий: {len(photo_paths)}"
                        if start_index == 0
                        else None
                    ),
                )

            continue

        with ExitStack() as stack:
            media: list[InputMediaPhoto] = []

            for index, photo_path in enumerate(chunk):
                photo = stack.enter_context(
                    photo_path.open("rb")
                )

                caption = None

                if start_index == 0 and index == 0:
                    caption = (
                        f"Готово 🖼\n"
                        f"Фотографий: {len(photo_paths)}"
                    )

                media.append(
                    InputMediaPhoto(
                        media=photo,
                        caption=caption,
                    )
                )

            await message.reply_media_group(
                media=media
            )


async def handle_download_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None or query.message is None:
        return

    await query.answer()

    message = query.message
    task_key = (
        f"{message.chat_id}:"
        f"{message.message_id}"
    )

    active_tasks: set[str] = context.bot_data.setdefault(
        "active_tasks",
        set(),
    )

    if task_key in active_tasks:
        await query.answer(
            "Загрузка уже выполняется ⏳",
            show_alert=True,
        )
        return

    url = context.user_data.get(
        f"url_{message.message_id}"
    )

    if not url:
        await query.edit_message_text(
            "Ссылка устарела. Отправь её ещё раз."
        )
        return

    active_tasks.add(task_key)

    loading_texts = {
        "download_video": "Скачиваю видео… ⏳",
        "download_audio": "Готовлю MP3… ⏳",
        "download_photos": "Скачиваю фотографии… ⏳",
    }

    await query.edit_message_text(
        loading_texts.get(
            query.data,
            "Обрабатываю… ⏳",
        )
    )

    try:
        with tempfile.TemporaryDirectory() as folder:
            if query.data == "download_photos":
                photos = await run_with_retry(
                    download_photos,
                    url,
                    folder,
                    status_message=message,
                )

                await send_photo_albums(
                    message,
                    photos,
                )

            elif query.data == "download_audio":
                (
                    mp3,
                    metadata,
                    cover,
                ) = await run_with_retry(
                    download_audio_as_mp3,
                    url,
                    folder,
                    status_message=message,
                )

                with ExitStack() as stack:
                    audio = stack.enter_context(
                        mp3.open("rb")
                    )

                    thumbnail = None

                    if cover and cover.exists():
                        thumbnail = stack.enter_context(
                            cover.open("rb")
                        )

                    await message.reply_audio(
                        audio=audio,
                        caption="MP3 готов 🎵",
                        filename=(
                            f"{metadata.title[:50]}.mp3"
                        ),
                        title=metadata.title,
                        performer=metadata.performer,
                        thumbnail=thumbnail,
                    )

            else:
                video = await run_with_retry(
                    download_video,
                    url,
                    folder,
                    status_message=message,
                )

                if not video.exists():
                    raise FileNotFoundError(
                        "Скачанный файл не найден"
                    )

                with video.open("rb") as video_file:
                    await message.reply_video(
                        video=video_file,
                        caption="Видео готово ✅",
                        supports_streaming=True,
                    )

        await message.delete()

    except Exception as error:
        error_text = str(error)

        print(
            f"Download error: {error_text}",
            flush=True,
        )

        await message.edit_text(
            "Не получилось скачать 😔\n\n"
            f"Причина:\n{error_text[:2500]}"
        )

    finally:
        active_tasks.discard(task_key)


def main() -> None:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_link,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            handle_download_choice,
            pattern=r"^download_(video|audio|photos)$",
        )
    )

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=WEBHOOK_URL,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
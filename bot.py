import asyncio
import html
import json
import os
import re
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx
import imageio_ffmpeg
import yt_dlp
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
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


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
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


def extract_json_objects(page_html: str) -> list[Any]:
    json_objects: list[Any] = []

    script_patterns = [
        (
            r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"'
            r"[^>]*>(.*?)</script>"
        ),
        r'<script[^>]+id="SIGI_STATE"[^>]*>(.*?)</script>',
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    ]

    for pattern in script_patterns:
        matches = re.findall(
            pattern,
            page_html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        for match in matches:
            cleaned = html.unescape(match.strip())

            try:
                json_objects.append(json.loads(cleaned))
            except json.JSONDecodeError:
                continue

    return json_objects


def urls_from_value(value: Any) -> list[str]:
    found: list[str] = []

    if isinstance(value, str):
        if value.startswith("http"):
            found.append(value)

    elif isinstance(value, list):
        for item in value:
            found.extend(urls_from_value(item))

    elif isinstance(value, dict):
        preferred_keys = (
            "urlList",
            "url_list",
            "url",
            "uri",
            "src",
        )

        for key in preferred_keys:
            if key in value:
                found.extend(urls_from_value(value[key]))

    return found


def find_photo_urls(data: Any) -> list[str]:
    found_urls: list[str] = []

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

                        candidates = urls_from_value(image_url)

                        if candidates:
                            found_urls.append(candidates[0])

            for child in value.values():
                walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)

    return list(dict.fromkeys(found_urls))


def find_music_urls(data: Any) -> list[str]:
    found_urls: list[str] = []

    music_container_names = {
        "music",
        "musicinfo",
        "music_info",
        "musicdetail",
        "music_detail",
    }

    play_url_names = {
        "playurl",
        "play_url",
        "playuri",
        "play_uri",
        "playurluri",
        "play_url_uri",
        "musicplayurl",
        "music_play_url",
    }

    def normalise_key(key: str) -> str:
        return key.replace("-", "_").lower()

    def walk(value: Any, inside_music: bool = False) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                clean_key = normalise_key(str(key))
                child_inside_music = (
                    inside_music
                    or clean_key in music_container_names
                )

                if clean_key in play_url_names:
                    found_urls.extend(urls_from_value(child))
                    continue

                if child_inside_music and "play" in clean_key:
                    candidates = urls_from_value(child)

                    for candidate in candidates:
                        lowered = candidate.lower()

                        if not any(
                            blocked in lowered
                            for blocked in (
                                "cover",
                                "avatar",
                                "image",
                                "thumbnail",
                            )
                        ):
                            found_urls.append(candidate)

                walk(child, child_inside_music)

        elif isinstance(value, list):
            for child in value:
                walk(child, inside_music)

    walk(data)

    return list(dict.fromkeys(found_urls))


def extract_fallback_photo_urls(page_html: str) -> list[str]:
    decoded = html.unescape(page_html)
    decoded = decoded.replace("\\u002F", "/")
    decoded = decoded.replace("\\/", "/")

    candidates = re.findall(
        r'https?://[^"\'\s<>]+',
        decoded,
    )

    image_urls: list[str] = []

    for candidate in candidates:
        cleaned = candidate.rstrip("\\,}]")
        lowered = cleaned.lower()

        looks_like_tiktok_image = (
            "tiktokcdn" in lowered
            or "byteimg" in lowered
            or "ibytedtos" in lowered
        )

        is_not_unwanted = not any(
            blocked in lowered
            for blocked in (
                "avatar",
                "profile",
                "music",
                "cover",
            )
        )

        if looks_like_tiktok_image and is_not_unwanted:
            image_urls.append(cleaned)

    return list(dict.fromkeys(image_urls))


def get_tiktok_post_assets(
    url: str,
) -> tuple[list[str], str | None, str]:
    with httpx.Client(
        headers=BROWSER_HEADERS,
        follow_redirects=True,
        timeout=30,
    ) as client:
        response = client.get(url)
        response.raise_for_status()

        final_url = str(response.url)
        page_html = response.text

    photo_urls: list[str] = []
    music_urls: list[str] = []

    for json_object in extract_json_objects(page_html):
        photo_urls.extend(find_photo_urls(json_object))
        music_urls.extend(find_music_urls(json_object))

    photo_urls = list(dict.fromkeys(photo_urls))
    music_urls = list(dict.fromkeys(music_urls))

    if not photo_urls:
        photo_urls = extract_fallback_photo_urls(page_html)

    music_url = music_urls[0] if music_urls else None

    return photo_urls, music_url, final_url


def download_video(
    url: str,
    folder: str,
) -> Path:
    output_template = os.path.join(
        folder,
        "%(title).80s-%(id)s.%(ext)s",
    )

    options: dict[str, Any] = {
        "outtmpl": output_template,
        "format": "best[ext=mp4]/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        downloaded_path = downloader.prepare_filename(info)

    path = Path(downloaded_path)

    if not path.exists():
        candidates = [
            file
            for file in Path(folder).iterdir()
            if file.is_file()
        ]

        if candidates:
            path = max(
                candidates,
                key=lambda item: item.stat().st_size,
            )

    return path


def download_audio_source(
    url: str,
    folder: str,
) -> Path:
    output_template = os.path.join(
        folder,
        "source-%(id)s.%(ext)s",
    )

    options: dict[str, Any] = {
        "outtmpl": output_template,
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        downloaded_path = downloader.prepare_filename(info)

    path = Path(downloaded_path)

    if path.exists():
        return path

    candidates = [
        file
        for file in Path(folder).iterdir()
        if file.is_file() and file.suffix.lower() != ".mp3"
    ]

    if not candidates:
        raise FileNotFoundError(
            "Исходная аудиодорожка не найдена"
        )

    return max(
        candidates,
        key=lambda item: item.stat().st_size,
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
        timeout=45,
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

    source_path = Path(folder) / f"photo_music{extension}"
    source_path.write_bytes(response.content)

    if source_path.stat().st_size < 5_000:
        raise RuntimeError(
            "TikTok отдал слишком маленький музыкальный файл"
        )

    return source_path


def convert_to_mp3(
    source_path: Path,
    folder: str,
) -> Path:
    output_path = Path(folder) / "IrisDownloader_audio.mp3"
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    command = [
        ffmpeg_exe,
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(output_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    if result.returncode != 0 or not output_path.exists():
        error_message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "FFmpeg не смог создать MP3"
        )

        raise RuntimeError(error_message[-2000:])

    return output_path


def download_audio_as_mp3(
    url: str,
    folder: str,
) -> Path:
    try:
        source_path = download_audio_source(
            url,
            folder,
        )

    except Exception as normal_audio_error:
        photo_urls, music_url, final_url = (
            get_tiktok_post_assets(url)
        )

        if not music_url:
            raise RuntimeError(
                "Не удалось найти музыку в фотопубликации.\n"
                f"Обычная загрузка тоже не сработала: "
                f"{normal_audio_error}"
            ) from normal_audio_error

        source_path = download_direct_music(
            music_url,
            final_url,
            folder,
        )

    return convert_to_mp3(
        source_path,
        folder,
    )


def download_photos(
    url: str,
    folder: str,
) -> list[Path]:
    photo_urls, _, final_url = get_tiktok_post_assets(url)

    if not photo_urls:
        raise RuntimeError(
            "TikTok не отдал список фотографий. "
            "Возможно, публикация закрыта."
        )

    headers = {
        **BROWSER_HEADERS,
        "Referer": final_url,
    }

    downloaded: list[Path] = []

    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=40,
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

                photo_path = Path(folder) / (
                    f"tiktok_photo_{index:02d}{extension}"
                )

                photo_path.write_bytes(response.content)

                if photo_path.stat().st_size < 5_000:
                    photo_path.unlink(missing_ok=True)
                    continue

                downloaded.append(photo_path)

            except httpx.HTTPError:
                continue

    if not downloaded:
        raise RuntimeError(
            "Ссылки на фотографии найдены, "
            "но TikTok не разрешил их скачать."
        )

    return downloaded


async def handle_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    text = update.message.text or ""
    url = find_link(text)

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
            with chunk[0].open("rb") as photo_file:
                caption = None

                if start_index == 0:
                    caption = (
                        f"Готово 🖼\n"
                        f"Фотографий: {len(photo_paths)}"
                    )

                await message.reply_photo(
                    photo=photo_file,
                    caption=caption,
                )

            continue

        with ExitStack() as stack:
            media: list[InputMediaPhoto] = []

            for index, photo_path in enumerate(chunk):
                photo_file = stack.enter_context(
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
                        media=photo_file,
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
    await query.answer()

    message = query.message
    url = context.user_data.get(
        f"url_{message.message_id}"
    )

    if not url:
        await query.edit_message_text(
            "Ссылка устарела. Отправь её ещё раз."
        )
        return

    choice = query.data

    loading_texts = {
        "download_video": "Скачиваю видео… ⏳",
        "download_audio": "Готовлю MP3… ⏳",
        "download_photos": "Скачиваю фотографии… ⏳",
    }

    await query.edit_message_text(
        loading_texts.get(
            choice,
            "Обрабатываю… ⏳",
        )
    )

    try:
        with tempfile.TemporaryDirectory() as folder:
            if choice == "download_photos":
                photo_paths = await asyncio.to_thread(
                    download_photos,
                    url,
                    folder,
                )

                await send_photo_albums(
                    message,
                    photo_paths,
                )

            elif choice == "download_audio":
                mp3_path = await asyncio.to_thread(
                    download_audio_as_mp3,
                    url,
                    folder,
                )

                with mp3_path.open("rb") as audio_file:
                    await message.reply_audio(
                        audio=audio_file,
                        caption="MP3 готов 🎵",
                        filename="IrisDownloader_audio.mp3",
                        title="TikTok audio",
                    )

            else:
                video_path = await asyncio.to_thread(
                    download_video,
                    url,
                    folder,
                )

                if not video_path.exists():
                    raise FileNotFoundError(
                        "Скачанный файл не найден"
                    )

                with video_path.open("rb") as video_file:
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
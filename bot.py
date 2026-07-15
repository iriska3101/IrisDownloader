import asyncio
import html
import json
import os
import re
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx
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
        "🎵 извлечь звук\n"
        "🖼 скачать фотографии"
    )


def find_link(text: str) -> str | None:
    match = re.search(r"https?://\S+", text)
    return match.group(0).rstrip(".,)") if match else None


def download_video_or_audio(
    url: str,
    folder: str,
    media_type: str,
) -> Path:
    output_template = os.path.join(
        folder,
        "%(title).80s-%(id)s.%(ext)s",
    )

    options: dict[str, Any] = {
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }

    if media_type == "audio":
        options["format"] = "bestaudio/best"
    else:
        options["format"] = "best[ext=mp4]/best"

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        downloaded_path = downloader.prepare_filename(info)

    return Path(downloaded_path)


def find_image_posts(data: Any) -> list[str]:
    """
    Ищет в JSON TikTok блоки imagePost и получает ссылки
    на оригинальные изображения.
    """
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

                        if not isinstance(image_url, dict):
                            continue

                        url_list = (
                            image_url.get("urlList")
                            or image_url.get("url_list")
                            or []
                        )

                        if isinstance(url_list, list):
                            for image_link in url_list:
                                if (
                                    isinstance(image_link, str)
                                    and image_link.startswith("http")
                                ):
                                    found_urls.append(image_link)
                                    break

            for child in value.values():
                walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(data)

    # Убираем повторы, сохраняя исходный порядок.
    return list(dict.fromkeys(found_urls))


def extract_json_objects(page_html: str) -> list[Any]:
    json_objects: list[Any] = []

    script_patterns = [
        r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>'
        r"(.*?)</script>",
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


def extract_fallback_image_urls(page_html: str) -> list[str]:
    """
    Запасной способ, если TikTok изменит JSON:
    ищет адреса CDN-картинок прямо в HTML.
    """
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

        looks_like_tiktok_image = (
            "tiktokcdn" in cleaned
            or "byteimg" in cleaned
            or "ibytedtos" in cleaned
        )

        is_not_avatar = not any(
            blocked in cleaned.lower()
            for blocked in (
                "avatar",
                "profile",
                "music",
                "cover",
            )
        )

        if looks_like_tiktok_image and is_not_avatar:
            image_urls.append(cleaned)

    return list(dict.fromkeys(image_urls))


def get_photo_urls(url: str) -> tuple[list[str], str]:
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

    for json_object in extract_json_objects(page_html):
        photo_urls.extend(find_image_posts(json_object))

    photo_urls = list(dict.fromkeys(photo_urls))

    if not photo_urls:
        photo_urls = extract_fallback_image_urls(page_html)

    if not photo_urls:
        raise RuntimeError(
            "TikTok не отдал список фотографий. "
            "Возможно, публикация закрыта или TikTok изменил страницу."
        )

    return photo_urls, final_url


def download_photos(
    url: str,
    folder: str,
) -> list[Path]:
    photo_urls, final_url = get_photo_urls(url)

    downloaded: list[Path] = []

    headers = {
        **BROWSER_HEADERS,
        "Referer": final_url,
    }

    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=40,
    ) as client:
        for index, photo_url in enumerate(photo_urls, start=1):
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

                # Не сохраняем подозрительно маленькие файлы.
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
        await update.message.reply_text("Пришли мне ссылку 🔗")
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎬 Видео",
                    callback_data="download_video",
                ),
                InlineKeyboardButton(
                    "🎵 Звук",
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

    context.user_data[f"url_{message.message_id}"] = url


async def send_photo_albums(
    message,
    photo_paths: list[Path],
) -> None:
    # Telegram принимает не более 10 файлов в одном альбоме.
    for start_index in range(0, len(photo_paths), 10):
        chunk = photo_paths[start_index : start_index + 10]

        # Если осталась одна картинка, отправляем её отдельно.
        if len(chunk) == 1:
            with chunk[0].open("rb") as photo_file:
                await message.reply_photo(
                    photo=photo_file,
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

            await message.reply_media_group(media=media)


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
        "download_audio": "Извлекаю звук… ⏳",
        "download_photos": "Скачиваю фотографии… ⏳",
    }

    await query.edit_message_text(
        loading_texts.get(choice, "Обрабатываю… ⏳")
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

            else:
                media_type = (
                    "audio"
                    if choice == "download_audio"
                    else "video"
                )

                file_path = await asyncio.to_thread(
                    download_video_or_audio,
                    url,
                    folder,
                    media_type,
                )

                if not file_path.exists():
                    raise FileNotFoundError(
                        "Скачанный файл не найден"
                    )

                with file_path.open("rb") as media_file:
                    if media_type == "audio":
                        await message.reply_audio(
                            audio=media_file,
                            caption="Звук готов 🎵",
                            filename=file_path.name,
                        )
                    else:
                        await message.reply_video(
                            video=media_file,
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
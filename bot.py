import asyncio
import os
import re
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    options = {
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


def download_photos(url: str, folder: str) -> list[Path]:
    command = [
        "gallery-dl",
        "--destination",
        folder,
        "--no-mtime",
        url,
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    image_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    photos = sorted(
        path
        for path in Path(folder).rglob("*")
        if path.is_file() and path.suffix.lower() in image_extensions
    )

    if not photos:
        error_message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "Фотографии в публикации не найдены"
        )
        raise RuntimeError(error_message)

    return photos


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
    # Telegram принимает максимум 10 файлов в одном альбоме.
    for start_index in range(0, len(photo_paths), 10):
        chunk = photo_paths[start_index : start_index + 10]

        with ExitStack() as stack:
            media = []

            for index, photo_path in enumerate(chunk):
                photo_file = stack.enter_context(photo_path.open("rb"))

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
    url = context.user_data.get(f"url_{message.message_id}")

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

                await send_photo_albums(message, photo_paths)

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
        print(f"Download error: {error_text}", flush=True)

        await message.edit_text(
            "Не получилось скачать 😔\n\n"
            f"Причина:\n{error_text[:2500]}"
        )


def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))

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
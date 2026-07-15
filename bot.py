import asyncio
import os
import re
import tempfile
from pathlib import Path

import yt_dlp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
        "Отправь мне ссылку на видео.\n"
        "После этого выбери: скачать видео или только звук."
    )


def find_link(text: str) -> str | None:
    match = re.search(r"https?://\S+", text)
    return match.group(0).rstrip(".,)") if match else None


def download_media(url: str, folder: str, media_type: str) -> Path:
    if media_type == "audio":
        output_template = os.path.join(folder, "%(title).80s-%(id)s.%(ext)s")

        options = {
            "outtmpl": output_template,
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
        }
    else:
        output_template = os.path.join(folder, "%(title).80s-%(id)s.%(ext)s")

        options = {
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

    return Path(downloaded_path)


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
                    "🎬 Скачать видео",
                    callback_data="download_video",
                ),
                InlineKeyboardButton(
                    "🎵 Скачать звук",
                    callback_data="download_audio",
                ),
            ]
        ]
    )

    message = await update.message.reply_text(
        "Что скачать?",
        reply_markup=keyboard,
    )

    context.user_data[f"url_{message.message_id}"] = url


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

    media_type = (
        "audio"
        if query.data == "download_audio"
        else "video"
    )

    loading_text = (
        "Извлекаю звук… ⏳"
        if media_type == "audio"
        else "Скачиваю видео… ⏳"
    )

    await query.edit_message_text(loading_text)

    try:
        with tempfile.TemporaryDirectory() as folder:
            file_path = await asyncio.to_thread(
                download_media,
                url,
                folder,
                media_type,
            )

            if not file_path.exists():
                raise FileNotFoundError("Скачанный файл не найден")

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
            pattern=r"^download_(video|audio)$",
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
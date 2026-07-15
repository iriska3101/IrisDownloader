import asyncio
import os
import re
import tempfile
from pathlib import Path

import yt_dlp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


BOT_TOKEN = os.environ["BOT_TOKEN"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! 👋\n\n"
        "Отправь мне ссылку на видео из TikTok, YouTube, Instagram "
        "или другой поддерживаемой социальной сети."
    )


def find_link(text: str) -> str | None:
    match = re.search(r"https?://\S+", text)
    return match.group(0) if match else None


def download_video(url: str, folder: str) -> Path:
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
        filename = downloader.prepare_filename(info)

    return Path(filename)


async def handle_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    text = update.message.text or ""
    url = find_link(text)

    if not url:
        await update.message.reply_text("Пришли мне ссылку на видео 🔗")
        return

    status = await update.message.reply_text("Скачиваю видео… ⏳")

    try:
        with tempfile.TemporaryDirectory() as folder:
            video_path = await asyncio.to_thread(download_video, url, folder)

            if not video_path.exists():
                raise FileNotFoundError("Скачанный файл не найден")

            with video_path.open("rb") as video:
                await update.message.reply_video(
                    video=video,
                    caption="Готово ✅",
                    supports_streaming=True,
                )

        await status.delete()

    except Exception as error:
        print(f"Download error: {error}")
        await status.edit_text(
            "Не получилось скачать это видео 😔\n"
            "Попробуй другую ссылку или повтори немного позже."
        )


def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)
    )

    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
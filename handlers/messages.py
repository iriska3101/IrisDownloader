from telegram import Update
from telegram.ext import ContextTypes


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
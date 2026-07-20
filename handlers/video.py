from telegram import Message

from services.video_progress import (
    download_video_with_progress,
)
from utils.progress import DownloadProgress
from utils.retry import run_with_retry


async def process_video_download(
    message: Message,
    url: str,
    folder: str,
) -> None:
    """
    Скачивает видео и временно отправляет его как документ.

    Это позволяет проверить исходный файл без обработки
    видеоплеером Telegram.
    """
    progress = DownloadProgress(
        message=message,
        title=(
            "⬇️ IriSSave\n\n"
            "🎬 Скачиваю видео…"
        ),
    )

    await progress.start()

    try:
        video_path = await run_with_retry(
            download_video_with_progress,
            url,
            folder,
            progress.hook,
            status_message=message,
        )
    finally:
        await progress.stop()

    if not video_path.exists():
        raise FileNotFoundError(
            "Скачанный видеофайл не найден"
        )

    await message.edit_text(
        "⬇️ IriSSave\n\n"
        "📤 Отправляю тестовый файл…"
    )

    with video_path.open("rb") as video_file:
        await message.reply_document(
            document=video_file,
            filename=video_path.name,
            caption=(
                "Тестовый файл IriSSave\n"
                "Проверяем пропорции и наличие звука"
            ),
            write_timeout=300,
            read_timeout=300,
            connect_timeout=60,
            pool_timeout=60,
        )

    await message.edit_text(
        "⬇️ IriSSave\n\n"
        "✅ Тестовый файл отправлен"
    )
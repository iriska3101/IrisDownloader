import asyncio

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

    Дополнительно выводит в Render Logs этап,
    на котором находится обработка.
    """
    progress = DownloadProgress(
        message=message,
        title=(
            "⬇️ IriSSave\n\n"
            "🎬 Скачиваю видео…"
        ),
    )

    print(
        "VIDEO HANDLER: запускаю прогресс",
        flush=True,
    )

    await progress.start()

    print(
        "VIDEO HANDLER: начинаю загрузку",
        flush=True,
    )

    try:
        video_path = await run_with_retry(
            download_video_with_progress,
            url,
            folder,
            progress.hook,
            status_message=message,
        )

        print(
            f"VIDEO HANDLER: загрузка завершена — {video_path}",
            flush=True,
        )

    finally:
        print(
            "VIDEO HANDLER: останавливаю прогресс",
            flush=True,
        )

        try:
            await asyncio.wait_for(
                progress.stop(),
                timeout=10,
            )

            print(
                "VIDEO HANDLER: прогресс остановлен",
                flush=True,
            )

        except asyncio.TimeoutError:
            print(
                "VIDEO HANDLER: progress.stop() завис — продолжаю без него",
                flush=True,
            )

        except Exception as error:
            print(
                "VIDEO HANDLER: ошибка остановки прогресса: "
                f"{type(error).__name__}: {error}",
                flush=True,
            )

    if not video_path.exists():
        raise FileNotFoundError(
            "Скачанный видеофайл не найден"
        )

    print(
        "VIDEO HANDLER: меняю сообщение на «Отправляю»",
        flush=True,
    )

    await message.edit_text(
        "⬇️ IriSSave\n\n"
        "📤 Отправляю тестовый файл…"
    )

    print(
        "VIDEO HANDLER: начинаю отправку документа",
        flush=True,
    )

    try:
        with video_path.open("rb") as video_file:
            await asyncio.wait_for(
                message.reply_document(
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
                ),
                timeout=360,
            )

    except asyncio.TimeoutError as error:
        print(
            "VIDEO HANDLER: отправка документа превысила 360 секунд",
            flush=True,
        )

        raise RuntimeError(
            "Telegram слишком долго отправлял видеофайл"
        ) from error

    print(
        "VIDEO HANDLER: документ отправлен",
        flush=True,
    )

    await message.edit_text(
        "⬇️ IriSSave\n\n"
        "✅ Тестовый файл отправлен"
    )

    print(
        "VIDEO HANDLER: обработка полностью завершена",
        flush=True,
    )
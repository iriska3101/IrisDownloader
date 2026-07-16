import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from handlers.media import (
    send_mp3,
    send_photo_albums,
)
from services.downloader import (
    download_audio_as_mp3,
    download_photos,
    download_video,
)
from utils.retry import run_with_retry


async def handle_download_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обрабатывает кнопки: видео, MP3 и фотографии."""
    query = update.callback_query

    if query is None or query.message is None:
        return

    await query.answer()

    message = query.message

    task_key = (
        f"{message.chat_id}:"
        f"{message.message_id}"
    )

    active_tasks: set[str] = (
        context.bot_data.setdefault(
            "active_tasks",
            set(),
        )
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
            "Ссылка устарела. "
            "Отправь её ещё раз."
        )
        return

    active_tasks.add(task_key)

    loading_texts = {
        "download_video": (
            "Скачиваю видео… ⏳"
        ),
        "download_audio": (
            "Готовлю MP3… ⏳"
        ),
        "download_photos": (
            "Скачиваю фотографии… ⏳"
        ),
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
                photos: list[Path] = (
                    await run_with_retry(
                        download_photos,
                        url,
                        folder,
                        status_message=message,
                    )
                )

                await send_photo_albums(
                    message,
                    photos,
                )

                await message.edit_text(
                    "✅ Фотографии успешно "
                    f"скачаны — {len(photos)} шт."
                )

            elif query.data == "download_audio":
                (
                    mp3_path,
                    metadata,
                    cover_path,
                ) = await run_with_retry(
                    download_audio_as_mp3,
                    url,
                    folder,
                    status_message=message,
                )

                await send_mp3(
                    message,
                    mp3_path,
                    metadata,
                    cover_path,
                )

                await message.edit_text(
                    "✅ MP3 успешно скачан 🎵"
                )

            else:
                video_path: Path = (
                    await run_with_retry(
                        download_video,
                        url,
                        folder,
                        status_message=message,
                    )
                )

                if not video_path.exists():
                    raise FileNotFoundError(
                        "Скачанный файл не найден"
                    )

                with video_path.open(
                    "rb"
                ) as video_file:
                    await message.reply_video(
                        video=video_file,
                        supports_streaming=True,
                    )

                await message.edit_text(
                    "✅ Видео успешно скачано"
                )

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


async def handle_search_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Скачивает выбранную песню из результатов поиска."""
    query = update.callback_query

    if query is None or query.message is None:
        return

    await query.answer()

    message = query.message

    callback_parts = (
        query.data or ""
    ).split(":")

    if len(callback_parts) != 3:
        await message.edit_text(
            "Не удалось прочитать "
            "выбранный результат."
        )
        return

    _, token, index_text = callback_parts

    try:
        index = int(index_text)

    except ValueError:
        await message.edit_text(
            "Некорректный номер результата."
        )
        return

    results = context.user_data.get(
        f"search_{token}"
    )

    if (
        not isinstance(results, list)
        or index < 0
        or index >= len(results)
    ):
        await message.edit_text(
            "Результаты поиска устарели. "
            "Напиши название песни ещё раз."
        )
        return

    selected = results[index]
    url = selected["url"]

    task_key = (
        f"{message.chat_id}:"
        f"{message.message_id}"
    )

    active_tasks: set[str] = (
        context.bot_data.setdefault(
            "active_tasks",
            set(),
        )
    )

    if task_key in active_tasks:
        await query.answer(
            "Загрузка уже выполняется ⏳",
            show_alert=True,
        )
        return

    active_tasks.add(task_key)

    await message.edit_text(
        "Готовлю выбранный MP3… ⏳"
    )

    try:
        with tempfile.TemporaryDirectory() as folder:
            (
                mp3_path,
                metadata,
                cover_path,
            ) = await run_with_retry(
                download_audio_as_mp3,
                url,
                folder,
                status_message=message,
            )

            await send_mp3(
                message,
                mp3_path,
                metadata,
                cover_path,
            )

            await message.edit_text(
                "✅ MP3 успешно скачан 🎵"
            )

    except Exception as error:
        error_text = str(error)

        print(
            "Search download error: "
            f"{error_text}",
            flush=True,
        )

        await message.edit_text(
            "Не получилось скачать "
            "выбранный трек 😔\n\n"
            f"Причина:\n{error_text[:2500]}"
        )

    finally:
        active_tasks.discard(task_key)
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from handlers.media import (
    send_mp3,
    send_photo_albums,
)
from handlers.video import process_video_download
from services.downloader import (
    download_audio_as_mp3,
    download_photos,
)
from utils.activity import ActivityIndicator
from utils.retry import run_with_retry


async def handle_download_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обрабатывает кнопки видео, MP3 и фотографий."""
    query = update.callback_query

    if query is None or query.message is None:
        return

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

    await query.answer()

    url = context.user_data.get(
        f"url_{message.message_id}"
    )

    if not url:
        await message.edit_text(
            "🌸 IriSSave\n\n"
            "Ссылка устарела. Отправь её ещё раз."
        )
        return

    active_tasks.add(task_key)

    activity_texts = {
        "download_video": "Подготавливаю видео…",
        "download_audio": "Подготавливаю MP3…",
        "download_photos": "Ищу фотографии…",
    }

    indicator = ActivityIndicator(
        message=message,
        text=activity_texts.get(
            query.data,
            "Обрабатываю запрос…",
        ),
    )

    await indicator.start()

    try:
        with tempfile.TemporaryDirectory() as folder:
            if query.data == "download_photos":
                await indicator.change_text(
                    "Скачиваю фотографии…"
                )

                photos: list[Path] = (
                    await run_with_retry(
                        download_photos,
                        url,
                        folder,
                        status_message=message,
                    )
                )

                await indicator.change_text(
                    "Отправляю фотографии…"
                )

                await send_photo_albums(
                    message,
                    photos,
                )

                await indicator.stop()

                await message.edit_text(
                    "🌸 IriSSave\n\n"
                    "✅ Фотографии успешно скачаны — "
                    f"{len(photos)} шт."
                )

            elif query.data == "download_audio":
                await indicator.change_text(
                    "Скачиваю и преобразую звук в MP3…"
                )

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

                await indicator.change_text(
                    "Добавляю название и обложку…"
                )

                await send_mp3(
                    message,
                    mp3_path,
                    metadata,
                    cover_path,
                )

                await indicator.stop()

                await message.edit_text(
                    "🌸 IriSSave\n\n"
                    "✅ MP3 готов к прослушиванию 🎵"
                )

            else:
                await indicator.stop()

                await process_video_download(
                    message=message,
                    url=url,
                    folder=folder,
                )

    except Exception as error:
        await indicator.stop()

        error_text = str(error)

        print(
            f"Download error: {error_text}",
            flush=True,
        )

        await message.edit_text(
            "🌸 IriSSave\n\n"
            "❌ Не получилось скачать\n\n"
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

    await query.answer()

    callback_parts = (
        query.data or ""
    ).split(":")

    if len(callback_parts) != 3:
        await message.edit_text(
            "🌸 IriSSave\n\n"
            "Не удалось прочитать выбранный результат."
        )
        return

    _, token, index_text = callback_parts

    try:
        index = int(index_text)

    except ValueError:
        await message.edit_text(
            "🌸 IriSSave\n\n"
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
            "🌸 IriSSave\n\n"
            "Результаты поиска устарели.\n"
            "Напиши название песни ещё раз."
        )
        return

    selected = results[index]
    url = selected["url"]

    active_tasks.add(task_key)

    indicator = ActivityIndicator(
        message=message,
        text="Подготавливаю выбранный MP3…",
    )

    await indicator.start()

    try:
        with tempfile.TemporaryDirectory() as folder:
            await indicator.change_text(
                "Скачиваю аудиодорожку…"
            )

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

            await indicator.change_text(
                "Добавляю название и обложку…"
            )

            await send_mp3(
                message,
                mp3_path,
                metadata,
                cover_path,
            )

            await indicator.stop()

            await message.edit_text(
                "🌸 IriSSave\n\n"
                "✅ MP3 готов к прослушиванию 🎵"
            )

    except Exception as error:
        await indicator.stop()

        error_text = str(error)

        print(
            f"Search download error: {error_text}",
            flush=True,
        )

        await message.edit_text(
            "🌸 IriSSave\n\n"
            "❌ Не получилось скачать трек\n\n"
            f"Причина:\n{error_text[:2500]}"
        )

    finally:
        active_tasks.discard(task_key)

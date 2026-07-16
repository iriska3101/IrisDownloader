import uuid

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from services.search import search_music_results
from utils.helpers import find_link
from utils.retry import run_with_retry


async def show_link_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
) -> None:
    """Показывает кнопки скачивания для присланной ссылки."""
    if update.message is None:
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


async def search_music(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query_text: str,
) -> None:
    """Ищет музыку и показывает пять вариантов кнопками."""
    if update.message is None:
        return

    status = await update.message.reply_text(
        "Ищу музыку… 🔎"
    )

    try:
        results = await run_with_retry(
            search_music_results,
            query_text,
            status_message=status,
        )

        if not results:
            await status.edit_text(
                "Ничего не нашла 😔\n"
                "Попробуй точнее написать название "
                "и исполнителя."
            )
            return

        token = uuid.uuid4().hex[:10]

        context.user_data[
            f"search_{token}"
        ] = results

        buttons: list[
            list[InlineKeyboardButton]
        ] = []

        for index, result in enumerate(results):
            title = result["title"]
            uploader = result["uploader"]

            if uploader:
                label = (
                    f"{index + 1}. "
                    f"{title} — {uploader}"
                )
            else:
                label = (
                    f"{index + 1}. {title}"
                )

            buttons.append(
                [
                    InlineKeyboardButton(
                        label[:60],
                        callback_data=(
                            f"search_audio:"
                            f"{token}:{index}"
                        ),
                    )
                ]
            )

        await status.edit_text(
            "Выбери нужный вариант:",
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
        )

    except Exception as error:
        print(
            f"Search error: {error}",
            flush=True,
        )

        await status.edit_text(
            "Не получилось выполнить поиск 😔\n\n"
            f"Причина:\n{str(error)[:1500]}"
        )


async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обрабатывает ссылки и поисковые запросы."""
    if update.message is None:
        return

    text = (
        update.message.text or ""
    ).strip()

    if not text:
        return

    url = find_link(text)

    if url:
        await show_link_menu(
            update,
            context,
            url,
        )
        return

    if len(text) < 2:
        await update.message.reply_text(
            "Напиши название песни "
            "и исполнителя."
        )
        return

    await search_music(
        update,
        context,
        text,
    )
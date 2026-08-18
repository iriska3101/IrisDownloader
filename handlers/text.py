import asyncio
import uuid
from urllib.parse import urlparse

import yt_dlp
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from services.downloader import get_tiktok_post_assets, resolve_tiktok_url
from services.search import search_music_results
from utils.helpers import find_link
from utils.retry import run_with_retry


def _probe_with_ytdlp(url: str) -> tuple[bool, bool]:
    """Возвращает (есть_видео, есть_фото) без скачивания файлов."""
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "retries": 1,
    }

    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)

    if not isinstance(info, dict):
        return True, True

    entries = info.get("entries")
    items = [item for item in entries if isinstance(item, dict)] if entries else [info]

    has_video = False
    has_photos = False

    for item in items:
        ext = str(item.get("ext") or "").lower()
        vcodec = item.get("vcodec")
        formats = item.get("formats") or []

        if ext in {"jpg", "jpeg", "png", "webp", "avif"}:
            has_photos = True

        if vcodec and vcodec != "none":
            has_video = True

        for fmt in formats:
            if not isinstance(fmt, dict):
                continue
            fmt_ext = str(fmt.get("ext") or "").lower()
            fmt_vcodec = fmt.get("vcodec")
            if fmt_ext in {"jpg", "jpeg", "png", "webp", "avif"}:
                has_photos = True
            if fmt_vcodec and fmt_vcodec != "none":
                has_video = True

    return has_video, has_photos


def _detect_media_types(url: str) -> tuple[bool, bool]:
    """Определяет доступные кнопки. При сомнении ничего полезного не прячет."""
    lowered = url.lower()
    host = urlparse(url).netloc.lower()

    # YouTube — всегда видео; превью не считаем фотопубликацией.
    if "youtube.com" in host or "youtu.be" in host:
        return True, False

    # Instagram Reels — видео. Для обычных постов проверяем метаданные.
    if "instagram.com" in host and "/reel" in lowered:
        return True, False

    # TikTok умеет явно различать /video/ и /photo/ после раскрытия short URL.
    if "tiktok.com" in host:
        resolved = resolve_tiktok_url(url)
        resolved_lower = resolved.lower()
        if "/video/" in resolved_lower:
            return True, False
        if "/photo/" in resolved_lower:
            return False, True

        try:
            photo_urls, _, final_url, _ = get_tiktok_post_assets(url)
            final_lower = final_url.lower()
            if "/photo/" in final_lower or photo_urls:
                return False, True
            if "/video/" in final_lower:
                return True, False
        except Exception:
            pass

    try:
        has_video, has_photos = _probe_with_ytdlp(url)
        if has_video or has_photos:
            return has_video, has_photos
    except Exception as error:
        print(
            "MEDIA PROBE: не удалось определить тип публикации | "
            f"{type(error).__name__}: {error}",
            flush=True,
        )

    # Не ломаем скачивание, если платформа скрыла метаданные.
    return True, True


async def show_link_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
) -> None:
    """Показывает только подходящие кнопки для присланной ссылки."""
    if update.message is None:
        return

    has_video, has_photos = await asyncio.to_thread(_detect_media_types, url)

    buttons: list[list[InlineKeyboardButton]] = []
    first_row: list[InlineKeyboardButton] = []

    if has_video:
        first_row.append(
            InlineKeyboardButton("🎬 Видео", callback_data="download_video")
        )

    # MP3 намеренно не зависит от типа поста: звук может быть и у видео,
    # и у фотопубликации/слайд-шоу.
    first_row.append(
        InlineKeyboardButton("🎵 MP3", callback_data="download_audio")
    )
    buttons.append(first_row)

    if has_photos:
        buttons.append(
            [InlineKeyboardButton("🖼 Фотографии", callback_data="download_photos")]
        )

    message = await update.message.reply_text(
        "Что скачать?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

    key = f"url_{message.message_id}"
    context.user_data[key] = url

    # Общая карта нужна для групп: кнопку под сообщением может нажать
    # не только автор ссылки, а любой участник чата.
    context.bot_data.setdefault("group_urls", {})[
        f"{message.chat_id}:{message.message_id}"
    ] = url


async def search_music(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query_text: str,
) -> None:
    """Ищет музыку и показывает пять вариантов кнопками."""
    if update.message is None:
        return

    status = await update.message.reply_text("Ищу музыку… 🔎")

    try:
        results = await run_with_retry(
            search_music_results,
            query_text,
            status_message=status,
        )

        if not results:
            await status.edit_text(
                "Ничего не нашла 😔\n"
                "Попробуй точнее написать название и исполнителя."
            )
            return

        token = uuid.uuid4().hex[:10]
        context.user_data[f"search_{token}"] = results
        buttons: list[list[InlineKeyboardButton]] = []

        for index, result in enumerate(results):
            title = result["title"]
            uploader = result["uploader"]
            label = (
                f"{index + 1}. {title} — {uploader}"
                if uploader
                else f"{index + 1}. {title}"
            )
            buttons.append(
                [InlineKeyboardButton(
                    label[:60],
                    callback_data=f"search_audio:{token}:{index}",
                )]
            )

        await status.edit_text(
            "Выбери нужный вариант:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    except Exception as error:
        print(f"Search error: {error}", flush=True)
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

    text = (update.message.text or "").strip()
    if not text:
        return

    url = find_link(text)
    if url:
        await show_link_menu(update, context, url)
        return

    # В группе обычную переписку полностью игнорируем.
    if update.message.chat.type in ("group", "supergroup"):
        return

    if len(text) < 2:
        await update.message.reply_text(
            "Напиши название песни и исполнителя."
        )
        return

    await search_music(update, context, text)

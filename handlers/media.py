import asyncio
from contextlib import ExitStack
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from telegram import (
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from telegram.error import NetworkError, TimedOut

from services.downloader import AudioMetadata


_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}

_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
}

# Telegram иногда успевает принять файл, но ответ API на Render
# приходит позже стандартного таймаута. Для медиа даём API больше времени.
_MEDIA_READ_TIMEOUT = 180.0
_MEDIA_WRITE_TIMEOUT = 180.0
_MEDIA_CONNECT_TIMEOUT = 30.0
_MEDIA_POOL_TIMEOUT = 30.0

_T = TypeVar("_T")


def _is_video(path: Path) -> bool:
    return (
        path.suffix.lower()
        in _VIDEO_EXTENSIONS
    )


def _is_image(path: Path) -> bool:
    return (
        path.suffix.lower()
        in _IMAGE_EXTENSIONS
    )


async def _send_with_retry(
    operation: Callable[[], Awaitable[_T]],
    *,
    label: str,
) -> _T | None:
    """
    Устойчиво отправляет медиа в Telegram.

    TimedOut не повторяем автоматически: Telegram мог уже принять файл,
    а повторная отправка создала бы дубль. Такой случай считаем
    неопределённым, но не валим всю публикацию после уже отправленного файла.

    Другие NetworkError повторяем один раз.
    """
    try:
        print(f"TELEGRAM SEND: start | {label}", flush=True)
        result = await operation()
        print(f"TELEGRAM SEND: ok | {label}", flush=True)
        return result

    except TimedOut as error:
        print(
            "TELEGRAM SEND: timeout after upload; "
            f"not retrying to avoid duplicate | {label} | "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
        return None

    except NetworkError as first_error:
        print(
            "TELEGRAM SEND: network error; retrying once | "
            f"{label} | {type(first_error).__name__}: {first_error}",
            flush=True,
        )
        await asyncio.sleep(2)

        try:
            result = await operation()
            print(f"TELEGRAM SEND: retry ok | {label}", flush=True)
            return result
        except TimedOut as error:
            print(
                "TELEGRAM SEND: retry timeout; "
                f"not retrying again | {label} | "
                f"{type(error).__name__}: {error}",
                flush=True,
            )
            return None


async def send_media_albums(
    message: Message,
    media_paths: list[Path],
) -> None:
    """
    Отправляет фотографии и видео
    альбомами по 10 элементов.
    """
    valid_paths = [
        path
        for path in media_paths
        if (
            path.exists()
            and (
                _is_image(path)
                or _is_video(path)
            )
        )
    ]

    if not valid_paths:
        raise RuntimeError(
            "Нет файлов для отправки."
        )

    for start_index in range(
        0,
        len(valid_paths),
        10,
    ):
        chunk = valid_paths[
            start_index : start_index + 10
        ]
        chunk_number = (start_index // 10) + 1

        if len(chunk) == 1:
            path = chunk[0]

            async def send_single() -> object:
                with path.open("rb") as media:
                    if _is_video(path):
                        return await message.reply_video(
                            video=media,
                            supports_streaming=True,
                            read_timeout=_MEDIA_READ_TIMEOUT,
                            write_timeout=_MEDIA_WRITE_TIMEOUT,
                            connect_timeout=_MEDIA_CONNECT_TIMEOUT,
                            pool_timeout=_MEDIA_POOL_TIMEOUT,
                        )

                    return await message.reply_photo(
                        photo=media,
                        read_timeout=_MEDIA_READ_TIMEOUT,
                        write_timeout=_MEDIA_WRITE_TIMEOUT,
                        connect_timeout=_MEDIA_CONNECT_TIMEOUT,
                        pool_timeout=_MEDIA_POOL_TIMEOUT,
                    )

            await _send_with_retry(
                send_single,
                label=f"single {path.name}",
            )
            continue

        async def send_group() -> object:
            with ExitStack() as stack:
                media_group = []

                for path in chunk:
                    media = stack.enter_context(
                        path.open("rb")
                    )

                    if _is_video(path):
                        media_group.append(
                            InputMediaVideo(
                                media=media,
                                supports_streaming=True,
                            )
                        )
                    else:
                        media_group.append(
                            InputMediaPhoto(
                                media=media,
                            )
                        )

                return await message.reply_media_group(
                    media=media_group,
                    read_timeout=_MEDIA_READ_TIMEOUT,
                    write_timeout=_MEDIA_WRITE_TIMEOUT,
                    connect_timeout=_MEDIA_CONNECT_TIMEOUT,
                    pool_timeout=_MEDIA_POOL_TIMEOUT,
                )

        await _send_with_retry(
            send_group,
            label=(
                f"album {chunk_number} "
                f"({len(chunk)} files)"
            ),
        )


async def send_photo_albums(
    message: Message,
    photo_paths: list[Path],
) -> None:
    """
    Совместимость со старым кодом.

    Старые вызовы продолжают работать.
    """
    await send_media_albums(
        message,
        photo_paths,
    )


async def send_mp3(
    message: Message,
    mp3_path: Path,
    metadata: AudioMetadata,
    cover_path: Path | None,
) -> None:
    """Отправляет MP3 с названием, исполнителем и обложкой."""

    async def send_audio() -> object:
        with ExitStack() as stack:
            audio = stack.enter_context(
                mp3_path.open("rb")
            )

            thumbnail = None

            if (
                cover_path
                and cover_path.exists()
            ):
                thumbnail = stack.enter_context(
                    cover_path.open("rb")
                )

            return await message.reply_audio(
                audio=audio,
                filename=(
                    f"{metadata.title[:50]}.mp3"
                ),
                title=metadata.title,
                performer=metadata.performer,
                thumbnail=thumbnail,
                read_timeout=_MEDIA_READ_TIMEOUT,
                write_timeout=_MEDIA_WRITE_TIMEOUT,
                connect_timeout=_MEDIA_CONNECT_TIMEOUT,
                pool_timeout=_MEDIA_POOL_TIMEOUT,
            )

    await _send_with_retry(
        send_audio,
        label=f"audio {mp3_path.name}",
    )

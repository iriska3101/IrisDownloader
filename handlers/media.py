from contextlib import ExitStack
from pathlib import Path

from telegram import (
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

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

        if len(chunk) == 1:
            path = chunk[0]

            with path.open("rb") as media:
                if _is_video(path):
                    await message.reply_video(
                        video=media,
                        supports_streaming=True,
                    )
                else:
                    await message.reply_photo(
                        photo=media,
                    )

            continue

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

            await message.reply_media_group(
                media=media_group
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

        await message.reply_audio(
            audio=audio,
            filename=(
                f"{metadata.title[:50]}.mp3"
            ),
            title=metadata.title,
            performer=metadata.performer,
            thumbnail=thumbnail,
        )
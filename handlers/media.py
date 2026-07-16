from contextlib import ExitStack
from pathlib import Path

from telegram import InputMediaPhoto, Message

from services.downloader import AudioMetadata


async def send_photo_albums(
    message: Message,
    photo_paths: list[Path],
) -> None:
    """Отправляет фотографии альбомами по 10 штук."""
    for start_index in range(
        0,
        len(photo_paths),
        10,
    ):
        chunk = photo_paths[
            start_index : start_index + 10
        ]

        if len(chunk) == 1:
            with chunk[0].open("rb") as photo:
                await message.reply_photo(
                    photo=photo,
                )

            continue

        with ExitStack() as stack:
            media: list[InputMediaPhoto] = []

            for photo_path in chunk:
                photo = stack.enter_context(
                    photo_path.open("rb")
                )

                media.append(
                    InputMediaPhoto(
                        media=photo,
                    )
                )

            await message.reply_media_group(
                media=media
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
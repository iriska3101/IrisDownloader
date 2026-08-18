import asyncio
import subprocess
from pathlib import Path

import imageio_ffmpeg
from telegram import Message

from services.tiktok_api_fallback import (
    download_tiktok_via_tikwm,
)
from services.video_progress import (
    download_video_with_progress,
)
from utils.progress import DownloadProgress
from utils.retry import run_with_retry


def _normalize_instagram_video(video_path: Path) -> Path:
    """
    Нормализует Instagram-видео перед отправкой в Telegram.

    Некоторые Reels приходят с нестандартным SAR/DAR. На iPhone
    Telegram может интерпретировать такие метаданные как растянутое
    изображение. Перекодируем только видеопоток с квадратным пикселем
    (SAR=1), сохраняя исходные ширину/высоту и звук.
    """
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    output_path = video_path.with_name(
        f"{video_path.stem}-telegram.mp4"
    )

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        "setsar=1",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    print(
        "INSTAGRAM NORMALIZE: исправляю SAR/DAR для Telegram",
        flush=True,
    )

    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=180,
        check=False,
    )

    if result.returncode != 0:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(
            "Не удалось нормализовать Instagram-видео: "
            f"{result.stderr.strip()[-1200:]}"
        )

    if (
        not output_path.exists()
        or output_path.stat().st_size <= 0
    ):
        raise RuntimeError(
            "После нормализации Instagram-видео файл не создан"
        )

    print(
        "INSTAGRAM NORMALIZE: готово | "
        f"size={output_path.stat().st_size}",
        flush=True,
    )

    return output_path


async def process_video_download(
    message: Message,
    url: str,
    folder: str,
) -> None:
    """
    Скачивает видео и отправляет его в Telegram как обычное видео.

    Для TikTok, если прямой способ и yt-dlp не сработали,
    пробует отдельный резервный TikWM API.
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
        try:
            video_path = await run_with_retry(
                download_video_with_progress,
                url,
                folder,
                progress.hook,
                status_message=message,
            )

        except Exception as primary_error:
            if "tiktok.com" not in url.lower():
                raise

            print(
                "VIDEO HANDLER: основной TikTok downloader не сработал — "
                "запускаю TikWM fallback",
                flush=True,
            )
            print(
                "VIDEO HANDLER: primary error: "
                f"{type(primary_error).__name__}: {primary_error}",
                flush=True,
            )

            try:
                video_path = await asyncio.to_thread(
                    download_tiktok_via_tikwm,
                    url,
                    folder,
                    progress.hook,
                )

            except Exception as fallback_error:
                print(
                    "VIDEO HANDLER: TikWM fallback тоже не сработал: "
                    f"{type(fallback_error).__name__}: {fallback_error}",
                    flush=True,
                )

                raise RuntimeError(
                    "TikTok не удалось скачать ни основным способом, "
                    "ни через резервный TikWM"
                ) from fallback_error

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

    if "instagram.com" in url.lower():
        video_path = await asyncio.to_thread(
            _normalize_instagram_video,
            video_path,
        )

    print(
        "VIDEO HANDLER: меняю сообщение на «Отправляю»",
        flush=True,
    )

    await message.edit_text(
        "⬇️ IriSSave\n\n"
        "📤 Отправляю видео…"
    )

    print(
        "VIDEO HANDLER: начинаю отправку видео",
        flush=True,
    )

    try:
        with video_path.open("rb") as video_file:
            await asyncio.wait_for(
                message.reply_video(
                    video=video_file,
                    filename=video_path.name,
                    caption="⬇️ Скачано через IriSSave",
                    supports_streaming=True,
                    write_timeout=300,
                    read_timeout=300,
                    connect_timeout=60,
                    pool_timeout=60,
                ),
                timeout=360,
            )

    except asyncio.TimeoutError as error:
        print(
            "VIDEO HANDLER: отправка видео превысила 360 секунд",
            flush=True,
        )

        raise RuntimeError(
            "Telegram слишком долго отправлял видео"
        ) from error

    print(
        "VIDEO HANDLER: видео отправлено",
        flush=True,
    )

    await message.edit_text(
        "⬇️ IriSSave\n\n"
        "✅ Готово"
    )

    print(
        "VIDEO HANDLER: обработка полностью завершена",
        flush=True,
    )

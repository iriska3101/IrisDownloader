import asyncio
import re
import subprocess
from pathlib import Path

import imageio_ffmpeg
from telegram import Message

from services.tiktok_api_fallback import download_tiktok_via_tikwm
from services.video_progress import download_video_with_progress
from utils.progress import DownloadProgress
from utils.retry import run_with_retry


def _probe_video_dimensions(video_path: Path) -> tuple[int | None, int | None]:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [ffmpeg, "-hide_banner", "-i", str(video_path)]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(
            "VIDEO PROBE: не удалось получить размеры | "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
        return None, None

    stderr = result.stderr or ""
    match = re.search(
        r"Video:.*?(\d{2,5})x(\d{2,5})"
        r"(?:\s+\[SAR\s+(\d+):(\d+)\s+DAR\s+(\d+):(\d+)\])?",
        stderr,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        print("VIDEO PROBE: строка размеров не найдена", flush=True)
        return None, None

    width = int(match.group(1))
    height = int(match.group(2))
    sar_num = int(match.group(3) or 1)
    sar_den = int(match.group(4) or 1)
    if sar_den <= 0:
        sar_den = 1

    display_width = max(1, round(width * sar_num / sar_den))
    display_height = height

    rotation_match = re.search(
        r"rotation\s+of\s+(-?\d+(?:\.\d+)?)\s+degrees",
        stderr,
        flags=re.IGNORECASE,
    )
    rotation = 0
    if rotation_match:
        try:
            rotation = round(float(rotation_match.group(1))) % 360
        except ValueError:
            rotation = 0

    if rotation in {90, 270}:
        display_width, display_height = display_height, display_width

    print(
        "VIDEO PROBE: "
        f"coded={width}x{height} | SAR={sar_num}:{sar_den} | "
        f"rotation={rotation} | telegram={display_width}x{display_height}",
        flush=True,
    )
    return display_width, display_height


def _prepare_instagram_video(video_path: Path) -> Path:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    output_path = video_path.with_name(f"{video_path.stem}-telegram.mp4")
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
        "-c",
        "copy",
        "-bsf:v",
        "h264_metadata=sample_aspect_ratio=1/1",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    print("INSTAGRAM PREPARE: быстрый remux без перекодирования", flush=True)
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(
            "INSTAGRAM PREPARE: remux пропущен | "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
        output_path.unlink(missing_ok=True)
        return video_path

    if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        print(
            "INSTAGRAM PREPARE: remux готов | "
            f"size={output_path.stat().st_size}",
            flush=True,
        )
        return output_path

    output_path.unlink(missing_ok=True)
    print(
        "INSTAGRAM PREPARE: использую исходный файл | "
        f"{result.stderr.strip()[-600:]}",
        flush=True,
    )
    return video_path


async def process_video_download(message: Message, url: str, folder: str) -> None:
    progress = DownloadProgress(
        message=message,
        title="⬇️ IriSSave\n\n🎬 Скачиваю видео…",
    )

    print("VIDEO HANDLER: запускаю прогресс", flush=True)
    await progress.start()
    print("VIDEO HANDLER: начинаю загрузку", flush=True)

    lowered_url = url.lower()
    is_tiktok = "tiktok.com" in lowered_url
    is_youtube = "youtube.com" in lowered_url or "youtu.be" in lowered_url

    if is_youtube:
        await message.edit_text(
            "⬇️ IriSSave\n\n🔎 Проверяю доступ к YouTube…"
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
            if is_tiktok:
                print(
                    "VIDEO HANDLER: основной TikTok downloader не сработал — запускаю TikWM fallback",
                    flush=True,
                )
                print(
                    "VIDEO HANDLER: primary error: "
                    f"{type(primary_error).__name__}: {primary_error}",
                    flush=True,
                )
                await message.edit_text(
                    "⬇️ IriSSave\n\n🔄 Пробую резервный способ TikTok…"
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
                        "TikTok не удалось скачать ни основным способом, ни через резервный TikWM"
                    ) from fallback_error

            elif is_youtube:
                print(
                    "VIDEO HANDLER: YouTube заблокировал анонимную сессию",
                    flush=True,
                )
                print(
                    "VIDEO HANDLER: primary error: "
                    f"{type(primary_error).__name__}: {primary_error}",
                    flush=True,
                )
                raise RuntimeError(
                    "YouTube требует авторизованную сессию. "
                    "Нужно настроить cookies для IriSSave."
                ) from primary_error
            else:
                raise

        print(f"VIDEO HANDLER: загрузка завершена — {video_path}", flush=True)

    finally:
        print("VIDEO HANDLER: останавливаю прогресс", flush=True)
        try:
            await asyncio.wait_for(progress.stop(), timeout=10)
            print("VIDEO HANDLER: прогресс остановлен", flush=True)
        except asyncio.TimeoutError:
            print("VIDEO HANDLER: progress.stop() завис — продолжаю без него", flush=True)
        except Exception as error:
            print(
                "VIDEO HANDLER: ошибка остановки прогресса: "
                f"{type(error).__name__}: {error}",
                flush=True,
            )

    if not video_path.exists():
        raise FileNotFoundError("Скачанный видеофайл не найден")

    if "instagram.com" in lowered_url:
        await message.edit_text("⬇️ IriSSave\n\n⚙️ Подготавливаю видео…")
        video_path = await asyncio.to_thread(_prepare_instagram_video, video_path)

    video_width, video_height = await asyncio.to_thread(
        _probe_video_dimensions,
        video_path,
    )

    print("VIDEO HANDLER: меняю сообщение на «Отправляю»", flush=True)
    await message.edit_text("⬇️ IriSSave\n\n📤 Отправляю видео…")
    print("VIDEO HANDLER: начинаю отправку видео", flush=True)

    try:
        with video_path.open("rb") as video_file:
            await asyncio.wait_for(
                message.reply_video(
                    video=video_file,
                    filename=video_path.name,
                    caption="⬇️ Скачано через IriSSave",
                    supports_streaming=True,
                    width=video_width,
                    height=video_height,
                    write_timeout=300,
                    read_timeout=300,
                    connect_timeout=60,
                    pool_timeout=60,
                ),
                timeout=360,
            )
    except asyncio.TimeoutError as error:
        print("VIDEO HANDLER: отправка видео превысила 360 секунд", flush=True)
        raise RuntimeError("Telegram слишком долго отправлял видео") from error

    print("VIDEO HANDLER: видео отправлено", flush=True)
    await message.edit_text("⬇️ IriSSave\n\n✅ Готово")
    print("VIDEO HANDLER: обработка полностью завершена", flush=True)

from pathlib import Path
from typing import Any, Callable

import httpx
import imageio_ffmpeg
import subprocess

from config import BROWSER_HEADERS


TELEGRAM_SAFE_SIZE = 48 * 1024 * 1024
TIKWM_API = "https://tikwm.com/api/"


def _collect_video_urls(data: Any) -> list[str]:
    """Собирает видео-URL из ответа TikWM с приоритетом HD/play."""
    preferred: list[str] = []
    fallback: list[str] = []

    def walk(value: Any, key_name: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, str(key).lower())
            return

        if isinstance(value, list):
            for child in value:
                walk(child, key_name)
            return

        if not isinstance(value, str) or not value.startswith("http"):
            return

        lowered = value.lower()
        key = key_name.replace("_", "").replace("-", "")

        if any(word in key for word in ("cover", "avatar", "music", "image")):
            return

        looks_video = (
            any(word in key for word in ("hdplay", "play", "video", "download"))
            or ".mp4" in lowered
            or "/video/" in lowered
        )

        if not looks_video:
            return

        if "hdplay" in key or key == "play":
            preferred.append(value)
        else:
            fallback.append(value)

    walk(data)
    return list(dict.fromkeys(preferred + fallback))


def _validate_video(path: Path) -> tuple[bool, str]:
    if not path.exists() or path.stat().st_size < 100_000:
        return False, "файл отсутствует или слишком маленький"

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as error:
        return False, f"ffmpeg error: {type(error).__name__}: {error}"

    if result.returncode != 0:
        return False, (result.stderr.strip() or "ffmpeg rejected")[-1200:]

    return True, "ok"


def download_tiktok_via_tikwm(
    url: str,
    folder: str,
    progress_hook: Callable[[dict[str, Any]], None],
) -> Path:
    """Резервная загрузка TikTok через сторонний TikWM API."""
    folder_path = Path(folder)
    folder_path.mkdir(parents=True, exist_ok=True)

    print("TIKTOK TIKWM: запрашиваю резервный API", flush=True)

    timeout = httpx.Timeout(connect=20.0, read=60.0, write=20.0, pool=20.0)
    headers = {
        **BROWSER_HEADERS,
        "Referer": "https://tikwm.com/",
    }

    with httpx.Client(headers=headers, follow_redirects=True, timeout=timeout) as client:
        response = client.get(
            TIKWM_API,
            params={"url": url, "hd": "1"},
        )
        response.raise_for_status()
        payload = response.json()

        if not isinstance(payload, dict):
            raise RuntimeError("TikWM вернул неожиданный ответ")

        code = payload.get("code")
        if code not in (None, 0):
            raise RuntimeError(
                f"TikWM API error {code}: {payload.get('msg') or 'unknown'}"
            )

        data = payload.get("data") or payload
        candidates = _collect_video_urls(data)

        print(
            f"TIKTOK TIKWM: найдено кандидатов: {len(candidates)}",
            flush=True,
        )

        if not candidates:
            raise RuntimeError("TikWM не вернул адрес видео")

        last_reason = "unknown"

        for index, media_url in enumerate(candidates, start=1):
            path = folder_path / f"tiktok-tikwm-{index}.mp4"

            try:
                print(
                    f"TIKTOK TIKWM CANDIDATE {index}/{len(candidates)}",
                    flush=True,
                )

                with client.stream("GET", media_url) as media_response:
                    media_response.raise_for_status()

                    raw_length = media_response.headers.get("content-length", "")
                    try:
                        total = int(raw_length or 0)
                    except ValueError:
                        total = 0

                    if total and total > TELEGRAM_SAFE_SIZE:
                        last_reason = "файл больше 48 МБ"
                        continue

                    downloaded = 0
                    with path.open("wb") as file:
                        for chunk in media_response.iter_bytes(chunk_size=256 * 1024):
                            if not chunk:
                                continue
                            file.write(chunk)
                            downloaded += len(chunk)

                            if downloaded > TELEGRAM_SAFE_SIZE:
                                raise RuntimeError("файл превысил 48 МБ")

                valid, reason = _validate_video(path)
                print(
                    f"TIKTOK TIKWM validation={'VALID' if valid else 'INVALID'}: {reason}",
                    flush=True,
                )

                if not valid:
                    last_reason = reason
                    path.unlink(missing_ok=True)
                    continue

                size = path.stat().st_size
                progress_hook(
                    {
                        "status": "finished",
                        "downloaded_bytes": size,
                        "total_bytes": size,
                        "filename": str(path),
                    }
                )

                print(
                    f"TIKTOK TIKWM: SUCCESS {path.name} | {size} bytes",
                    flush=True,
                )
                return path

            except Exception as error:
                last_reason = f"{type(error).__name__}: {error}"
                path.unlink(missing_ok=True)
                print(
                    f"TIKTOK TIKWM candidate error: {last_reason}",
                    flush=True,
                )

    raise RuntimeError(
        "TikWM не смог скачать воспроизводимое видео: " + last_reason
    )

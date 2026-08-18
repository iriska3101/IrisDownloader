import re
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import httpx
import imageio_ffmpeg


TELEGRAM_SAFE_SIZE = 48 * 1024 * 1024

PIPED_API_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.leptons.xyz",
    "https://pipedapi.nosebs.ru",
    "https://pipedapi-libre.kavin.rocks",
    "https://piped-api.privacy.com.de",
    "https://pipedapi.adminforge.de",
    "https://api.piped.yt",
    "https://pipedapi.drgns.space",
    "https://pipedapi.owo.si",
    "https://pipedapi.ducks.party",
    "https://api.piped.private.coffee",
]


def _extract_youtube_video_id(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")

    if host.endswith("youtu.be") and path:
        return path.split("/", 1)[0]

    if "youtube.com" in host:
        if path.startswith("shorts/"):
            return path.split("/", 1)[1].split("/", 1)[0]
        if path.startswith("embed/"):
            return path.split("/", 1)[1].split("/", 1)[0]

        query_id = parse_qs(parsed.query).get("v", [""])[0]
        if query_id:
            return query_id

    match = re.search(r"(?:v=|shorts/|youtu\.be/)([A-Za-z0-9_-]{6,})", url)
    if match:
        return match.group(1)

    raise RuntimeError("Не удалось определить ID YouTube-видео")


def _stream_score(stream: dict[str, Any]) -> tuple[int, int, int]:
    height = int(stream.get("height") or 0)
    fps = int(stream.get("fps") or 0)
    bitrate = int(stream.get("bitrate") or 0)
    return height, fps, bitrate


def _audio_score(stream: dict[str, Any]) -> int:
    return int(stream.get("bitrate") or 0)


def _select_combined_mp4(data: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []

    for stream in data.get("videoStreams") or []:
        if not isinstance(stream, dict):
            continue

        url = stream.get("url")
        mime = str(stream.get("mimeType") or "").lower()
        video_only = bool(stream.get("videoOnly"))
        height = int(stream.get("height") or 0)

        if not isinstance(url, str) or not url.startswith("http"):
            continue
        if video_only:
            continue
        if "video/mp4" not in mime:
            continue
        if height and height > 1080:
            continue

        candidates.append(stream)

    if not candidates:
        return None

    return max(candidates, key=_stream_score)


def _select_video_only_mp4(data: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []

    for stream in data.get("videoStreams") or []:
        if not isinstance(stream, dict):
            continue

        url = stream.get("url")
        mime = str(stream.get("mimeType") or "").lower()
        height = int(stream.get("height") or 0)

        if not isinstance(url, str) or not url.startswith("http"):
            continue
        if not bool(stream.get("videoOnly")):
            continue
        if "video/mp4" not in mime:
            continue
        if height and height > 1080:
            continue

        candidates.append(stream)

    if not candidates:
        return None

    return max(candidates, key=_stream_score)


def _select_audio_m4a(data: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []

    for stream in data.get("audioStreams") or []:
        if not isinstance(stream, dict):
            continue

        url = stream.get("url")
        mime = str(stream.get("mimeType") or "").lower()

        if not isinstance(url, str) or not url.startswith("http"):
            continue

        if not any(token in mime for token in ("audio/mp4", "m4a", "mp4a")):
            continue

        candidates.append(stream)

    if not candidates:
        return None

    return max(candidates, key=_audio_score)


def _download_stream(
    stream_url: str,
    output_path: Path,
    progress_hook: Callable[[dict[str, Any]], None] | None = None,
    size_limit: int = TELEGRAM_SAFE_SIZE,
) -> Path:
    timeout = httpx.Timeout(
        connect=20.0,
        read=120.0,
        write=20.0,
        pool=20.0,
    )

    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        with client.stream("GET", stream_url) as response:
            response.raise_for_status()

            content_type = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )

            if content_type.startswith(("text/", "application/json")):
                raise RuntimeError(
                    f"Piped proxy вернул неожиданный content-type: {content_type}"
                )

            raw_length = response.headers.get("content-length", "")
            try:
                total = int(raw_length or 0)
            except ValueError:
                total = 0

            if total and total > size_limit:
                raise RuntimeError(
                    f"Поток Piped больше допустимого размера: {total} bytes"
                )

            downloaded = 0
            with output_path.open("wb") as file:
                for chunk in response.iter_bytes(chunk_size=256 * 1024):
                    if not chunk:
                        continue

                    file.write(chunk)
                    downloaded += len(chunk)

                    if downloaded > size_limit:
                        raise RuntimeError("Поток Piped превысил допустимый размер")

                    if progress_hook is not None:
                        progress_hook(
                            {
                                "status": "downloading",
                                "downloaded_bytes": downloaded,
                                "total_bytes": total or None,
                                "filename": str(output_path),
                            }
                        )

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError("Piped не создал файл потока")

    return output_path


def _merge_video_audio(video_path: Path, audio_path: Path, output_path: Path) -> Path:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=90,
        check=False,
    )

    if result.returncode != 0:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(
            "ffmpeg не смог объединить YouTube video+audio: "
            f"{result.stderr.strip()[-1000:]}"
        )

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError("После объединения YouTube-файл не создан")

    if output_path.stat().st_size > TELEGRAM_SAFE_SIZE:
        output_path.unlink(missing_ok=True)
        raise RuntimeError("Объединённый YouTube-файл больше 48 МБ")

    return output_path


def download_youtube_via_piped(
    url: str,
    folder: str,
    progress_hook: Callable[[dict[str, Any]], None],
) -> Path:
    video_id = _extract_youtube_video_id(url)
    folder_path = Path(folder)
    folder_path.mkdir(parents=True, exist_ok=True)

    last_error: Exception | None = None

    for index, api_base in enumerate(PIPED_API_INSTANCES, start=1):
        try:
            print(
                "YOUTUBE PIPED: пробую instance "
                f"{index}/{len(PIPED_API_INSTANCES)} | {api_base}",
                flush=True,
            )

            response = httpx.get(
                f"{api_base.rstrip('/')}/streams/{video_id}",
                timeout=30.0,
                follow_redirects=True,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 IriSSave/1.0",
                },
            )
            response.raise_for_status()
            data = response.json()

            if not isinstance(data, dict):
                raise RuntimeError("Piped API вернул неожиданный ответ")

            if data.get("livestream"):
                raise RuntimeError("Прямые эфиры YouTube пока не поддерживаются")

            combined = _select_combined_mp4(data)
            output_path = folder_path / f"youtube-piped-{video_id}.mp4"
            output_path.unlink(missing_ok=True)

            if combined is not None:
                stream_url = combined.get("url")
                if not isinstance(stream_url, str):
                    raise RuntimeError("Piped combined stream URL отсутствует")

                print(
                    "YOUTUBE PIPED: найден готовый MP4 со звуком | "
                    f"quality={combined.get('quality')} | "
                    f"size={combined.get('width')}x{combined.get('height')} | "
                    f"fps={combined.get('fps')}",
                    flush=True,
                )

                result = _download_stream(
                    stream_url=stream_url,
                    output_path=output_path,
                    progress_hook=progress_hook,
                )

            else:
                video_stream = _select_video_only_mp4(data)
                audio_stream = _select_audio_m4a(data)

                if video_stream is None or audio_stream is None:
                    raise RuntimeError(
                        "Piped не отдал ни combined MP4, ни подходящую пару video+audio"
                    )

                print(
                    "YOUTUBE PIPED: combined MP4 нет — скачиваю video+audio отдельно | "
                    f"video={video_stream.get('quality')} "
                    f"{video_stream.get('width')}x{video_stream.get('height')} | "
                    f"audio={audio_stream.get('bitrate')}",
                    flush=True,
                )

                video_part = folder_path / f"youtube-piped-{video_id}-video.mp4"
                audio_part = folder_path / f"youtube-piped-{video_id}-audio.m4a"
                video_part.unlink(missing_ok=True)
                audio_part.unlink(missing_ok=True)

                _download_stream(
                    stream_url=str(video_stream["url"]),
                    output_path=video_part,
                    progress_hook=progress_hook,
                    size_limit=TELEGRAM_SAFE_SIZE,
                )

                remaining = max(
                    1_000_000,
                    TELEGRAM_SAFE_SIZE - video_part.stat().st_size,
                )

                _download_stream(
                    stream_url=str(audio_stream["url"]),
                    output_path=audio_part,
                    progress_hook=None,
                    size_limit=remaining,
                )

                result = _merge_video_audio(
                    video_path=video_part,
                    audio_path=audio_part,
                    output_path=output_path,
                )

                video_part.unlink(missing_ok=True)
                audio_part.unlink(missing_ok=True)

                size = result.stat().st_size
                progress_hook(
                    {
                        "status": "finished",
                        "downloaded_bytes": size,
                        "total_bytes": size,
                        "filename": str(result),
                    }
                )

            print(
                "YOUTUBE PIPED: SUCCESS | "
                f"size={result.stat().st_size} bytes",
                flush=True,
            )
            return result

        except Exception as error:
            last_error = error
            print(
                "YOUTUBE PIPED: instance failed | "
                f"{type(error).__name__}: {error}",
                flush=True,
            )

    raise RuntimeError(
        "YouTube не удалось скачать через Piped fallback"
    ) from last_error

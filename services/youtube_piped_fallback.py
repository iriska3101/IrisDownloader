import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import httpx


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


def _download_stream(
    stream_url: str,
    output_path: Path,
    progress_hook: Callable[[dict[str, Any]], None],
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

            if total and total > TELEGRAM_SAFE_SIZE:
                raise RuntimeError(
                    f"YouTube-файл через Piped больше 48 МБ: {total} bytes"
                )

            downloaded = 0
            with output_path.open("wb") as file:
                for chunk in response.iter_bytes(chunk_size=256 * 1024):
                    if not chunk:
                        continue

                    file.write(chunk)
                    downloaded += len(chunk)

                    if downloaded > TELEGRAM_SAFE_SIZE:
                        raise RuntimeError(
                            "YouTube-файл через Piped превысил 48 МБ"
                        )

                    progress_hook(
                        {
                            "status": "downloading",
                            "downloaded_bytes": downloaded,
                            "total_bytes": total or None,
                            "filename": str(output_path),
                        }
                    )

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError("Piped не создал видеофайл")

    size = output_path.stat().st_size
    progress_hook(
        {
            "status": "finished",
            "downloaded_bytes": size,
            "total_bytes": size,
            "filename": str(output_path),
        }
    )

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

            stream = _select_combined_mp4(data)
            if stream is None:
                raise RuntimeError("Piped не отдал готовый MP4 со звуком")

            stream_url = stream.get("url")
            if not isinstance(stream_url, str):
                raise RuntimeError("Piped stream URL отсутствует")

            print(
                "YOUTUBE PIPED: найден combined MP4 | "
                f"quality={stream.get('quality')} | "
                f"size={stream.get('width')}x{stream.get('height')} | "
                f"fps={stream.get('fps')}",
                flush=True,
            )

            output_path = folder_path / f"youtube-piped-{video_id}.mp4"
            output_path.unlink(missing_ok=True)

            result = _download_stream(
                stream_url=stream_url,
                output_path=output_path,
                progress_hook=progress_hook,
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

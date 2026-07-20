from pathlib import Path

import httpx

from config import BROWSER_HEADERS
from services.downloader import get_tiktok_post_assets


def download_tiktok_photos(
    url: str,
    folder: str,
) -> list[Path]:
    """
    Загружает фотографии из TikTok-публикации.
    Рабочая логика перенесена без изменений.
    """
    (
        photo_urls,
        _,
        final_url,
        _,
    ) = get_tiktok_post_assets(url)

    if not photo_urls:
        raise RuntimeError(
            "TikTok не отдал список фотографий."
        )

    headers = {
        **BROWSER_HEADERS,
        "Referer": final_url,
    }

    downloaded: list[Path] = []

    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(60),
    ) as client:
        for index, photo_url in enumerate(
            photo_urls,
            start=1,
        ):
            try:
                response = client.get(photo_url)
                response.raise_for_status()

                content_type = response.headers.get(
                    "content-type",
                    "",
                ).lower()

                if "png" in content_type:
                    extension = ".png"
                elif "webp" in content_type:
                    extension = ".webp"
                else:
                    extension = ".jpg"

                path = Path(folder) / (
                    "tiktok_photo_"
                    f"{index:02d}"
                    f"{extension}"
                )

                path.write_bytes(response.content)

                if path.stat().st_size < 5_000:
                    path.unlink(missing_ok=True)
                    continue

                downloaded.append(path)

            except httpx.HTTPError:
                continue

    if not downloaded:
        raise RuntimeError(
            "TikTok не разрешил скачать фотографии."
        )

    return downloaded

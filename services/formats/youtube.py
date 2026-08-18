import shutil
import subprocess
from pathlib import Path
from typing import Any


BGUTIL_VERSION = "1.3.1"
BGUTIL_ROOT = Path("/tmp/bgutil-ytdlp-pot-provider")
BGUTIL_SERVER = BGUTIL_ROOT / "server"


def _prepare_bgutil_provider() -> str | None:
    """
    Готовит локальный BgUtils PO Token provider для yt-dlp.

    Плагин ставится через requirements.txt, а сам JS provider
    разворачивается в /tmp при первом YouTube-запросе. Если в
    окружении Render нет Node/npm/git, просто возвращаем None и
    yt-dlp попробует mweb без локального provider.
    """
    built_file = BGUTIL_SERVER / "build" / "main.js"

    if built_file.exists():
        print(
            "YOUTUBE POT: BgUtils provider уже готов",
            flush=True,
        )
        return str(BGUTIL_SERVER)

    node = shutil.which("node")
    npm = shutil.which("npm")
    git = shutil.which("git")

    if not node or not npm or not git:
        print(
            "YOUTUBE POT: Node/npm/git недоступны в Render; "
            "продолжаю без локального provider",
            flush=True,
        )
        return None

    try:
        if BGUTIL_ROOT.exists():
            shutil.rmtree(BGUTIL_ROOT, ignore_errors=True)

        print(
            "YOUTUBE POT: устанавливаю BgUtils provider",
            flush=True,
        )

        subprocess.run(
            [
                git,
                "clone",
                "--depth",
                "1",
                "--branch",
                BGUTIL_VERSION,
                "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git",
                str(BGUTIL_ROOT),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90,
        )

        subprocess.run(
            [npm, "ci"],
            cwd=BGUTIL_SERVER,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,
        )

        subprocess.run(
            ["npx", "tsc"],
            cwd=BGUTIL_SERVER,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )

    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        print(
            "YOUTUBE POT: provider не удалось подготовить: "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
        return None

    if not built_file.exists():
        print(
            "YOUTUBE POT: build/main.js не создан",
            flush=True,
        )
        return None

    print(
        "YOUTUBE POT: BgUtils provider готов",
        flush=True,
    )
    return str(BGUTIL_SERVER)


def get_youtube_options() -> dict[str, Any]:
    """
    Настройки для YouTube и YouTube Shorts.

    Используем рекомендуемый mweb-клиент. Если локальный BgUtils
    provider удалось подготовить, его plugin автоматически генерирует
    PO Token для конкретного видео.
    """
    provider_home = _prepare_bgutil_provider()

    extractor_args: dict[str, dict[str, list[str]]] = {
        "youtube": {
            "player_client": ["mweb"],
        },
    }

    if provider_home:
        extractor_args["youtubepot-bgutilscript"] = {
            "server_home": [provider_home],
        }

    return {
        "format": (
            "bv*[ext=mp4]+ba[ext=m4a]/"
            "b[ext=mp4]/"
            "bv*+ba/b"
        ),
        "format_sort": [
            "res",
            "fps",
            "hasaud",
        ],
        "merge_output_format": "mp4",
        "extractor_args": extractor_args,
    }

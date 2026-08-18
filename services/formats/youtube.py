import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


BGUTIL_VERSION = "1.3.1"
BGUTIL_ROOT = Path("/tmp/bgutil-ytdlp-pot-provider")
BGUTIL_SERVER = BGUTIL_ROOT / "server"
BGUTIL_BUILT_FILE = BGUTIL_SERVER / "build" / "main.js"
BGUTIL_READY_MARKER = BGUTIL_ROOT / ".irissave-ready"


def _run_provider_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)

    # На Render NODE_ENV нередко production. Тогда npm может
    # пропустить devDependencies, а TypeScript нужен именно для build.
    env.pop("NODE_ENV", None)
    env["NPM_CONFIG_PRODUCTION"] = "false"

    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        env=env,
    )


def _prepare_bgutil_provider() -> str | None:
    """
    Готовит локальный BgUtils PO Token provider для yt-dlp.

    Готовым считаем provider только после полностью успешного build.
    Один лишь build/main.js недостаточен: TypeScript может успеть
    записать часть build и затем завершиться с ошибкой.
    """
    if (
        BGUTIL_READY_MARKER.exists()
        and BGUTIL_BUILT_FILE.exists()
    ):
        print(
            "YOUTUBE POT: BgUtils provider уже готов",
            flush=True,
        )
        return str(BGUTIL_SERVER)

    node = shutil.which("node")
    npm = shutil.which("npm")
    npx = shutil.which("npx")
    git = shutil.which("git")

    if not node or not npm or not npx or not git:
        print(
            "YOUTUBE POT: Node/npm/npx/git недоступны в Render; "
            "продолжаю без локального provider",
            flush=True,
        )
        return None

    try:
        if BGUTIL_ROOT.exists():
            shutil.rmtree(
                BGUTIL_ROOT,
                ignore_errors=True,
            )

        print(
            "YOUTUBE POT: устанавливаю BgUtils provider",
            flush=True,
        )

        clone = subprocess.run(
            [
                git,
                "clone",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                BGUTIL_VERSION,
                "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git",
                str(BGUTIL_ROOT),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90,
        )

        if clone.stderr.strip():
            print(
                "YOUTUBE POT: git: "
                f"{clone.stderr.strip()[-600:]}",
                flush=True,
            )

        npm_result = _run_provider_command(
            [
                npm,
                "ci",
                "--include=dev",
                "--no-audit",
                "--no-fund",
            ],
            cwd=BGUTIL_SERVER,
            timeout=180,
        )

        if npm_result.stderr.strip():
            print(
                "YOUTUBE POT: npm: "
                f"{npm_result.stderr.strip()[-800:]}",
                flush=True,
            )

        tsc_result = _run_provider_command(
            [npx, "tsc"],
            cwd=BGUTIL_SERVER,
            timeout=180,
        )

        if tsc_result.stderr.strip():
            print(
                "YOUTUBE POT: tsc: "
                f"{tsc_result.stderr.strip()[-1200:]}",
                flush=True,
            )

    except subprocess.CalledProcessError as error:
        stderr = (
            error.stderr.strip()
            if isinstance(error.stderr, str)
            else ""
        )
        stdout = (
            error.stdout.strip()
            if isinstance(error.stdout, str)
            else ""
        )

        print(
            "YOUTUBE POT: provider не удалось подготовить: "
            f"{type(error).__name__}: {error}",
            flush=True,
        )

        if stderr:
            print(
                "YOUTUBE POT BUILD STDERR: "
                f"{stderr[-2000:]}",
                flush=True,
            )

        if stdout:
            print(
                "YOUTUBE POT BUILD STDOUT: "
                f"{stdout[-1200:]}",
                flush=True,
            )

        shutil.rmtree(
            BGUTIL_ROOT,
            ignore_errors=True,
        )
        return None

    except (
        OSError,
        subprocess.TimeoutExpired,
    ) as error:
        print(
            "YOUTUBE POT: provider не удалось подготовить: "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
        shutil.rmtree(
            BGUTIL_ROOT,
            ignore_errors=True,
        )
        return None

    if not BGUTIL_BUILT_FILE.exists():
        print(
            "YOUTUBE POT: build/main.js не создан",
            flush=True,
        )
        shutil.rmtree(
            BGUTIL_ROOT,
            ignore_errors=True,
        )
        return None

    BGUTIL_READY_MARKER.write_text(
        "ok\n",
        encoding="utf-8",
    )

    print(
        "YOUTUBE POT: BgUtils provider готов",
        flush=True,
    )
    return str(BGUTIL_SERVER)


def get_youtube_options() -> dict[str, Any]:
    """
    Настройки для YouTube и YouTube Shorts.

    Используем mweb-клиент. Если BgUtils provider полностью собран,
    его plugin сможет генерировать PO Token для конкретного видео.
    """
    provider_home = _prepare_bgutil_provider()

    extractor_args: dict[str, dict[str, list[str]]] = {
        "youtube": {
            "player_client": ["mweb"],
        },
    }

    if provider_home:
        # Для script-provider нужен путь к generate_once.js.
        script_path = (
            Path(provider_home)
            / "build"
            / "generate_once.js"
        )

        if script_path.exists():
            extractor_args["youtubepot-bgutilscript"] = {
                "script_path": [str(script_path)],
            }
            print(
                "YOUTUBE POT: script provider подключён",
                flush=True,
            )
        else:
            print(
                "YOUTUBE POT: generate_once.js не найден",
                flush=True,
            )

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

import base64
import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from services.diagnostics import get_public_logs, sanitize_line


GITHUB_REPOSITORY = "iriska3101/IrisDownloader"
GITHUB_DIAGNOSTICS_BRANCH = "diagnostics"
GITHUB_DIAGNOSTICS_PATH = "diagnostics/latest.json"


def _github_token() -> str | None:
    token = (
        os.getenv("IRISSAVE_GITHUB_TOKEN", "").strip()
        or os.getenv("GITHUB_TOKEN", "").strip()
    )
    return token or None


def _request_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "IriSSave-Diagnostics/1.0",
    }


def publish_diagnostic_snapshot(error_summary: str | None = None) -> bool:
    """
    Публикует безопасный диагностический snapshot в отдельную ветку GitHub.

    Ветка diagnostics не используется Render для deploy, поэтому запись
    snapshot не запускает новый deploy. Если токен не настроен или GitHub
    временно недоступен, основная работа бота не прерывается.
    """
    token = _github_token()
    if not token:
        print(
            "IRISSAVE DIAGNOSTICS GITHUB: токен не настроен",
            flush=True,
        )
        return False

    safe_error = sanitize_line(error_summary or "")[:1800]
    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "service": "IriSSave",
        "status": "error" if safe_error else "snapshot",
        "error": safe_error or None,
        "lines": get_public_logs(150),
    }

    content = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    ) + "\n"

    api_url = (
        "https://api.github.com/repos/"
        f"{GITHUB_REPOSITORY}/contents/{GITHUB_DIAGNOSTICS_PATH}"
    )

    timeout = httpx.Timeout(
        connect=15.0,
        read=20.0,
        write=20.0,
        pool=15.0,
    )

    try:
        with httpx.Client(
            timeout=timeout,
            headers=_request_headers(token),
            follow_redirects=True,
        ) as client:
            current_sha: str | None = None

            lookup = client.get(
                api_url,
                params={"ref": GITHUB_DIAGNOSTICS_BRANCH},
            )

            if lookup.status_code == 200:
                data = lookup.json()
                if isinstance(data, dict):
                    sha = data.get("sha")
                    if isinstance(sha, str) and sha:
                        current_sha = sha
            elif lookup.status_code != 404:
                raise RuntimeError(
                    "GitHub snapshot lookup failed: "
                    f"HTTP {lookup.status_code}"
                )

            body: dict[str, Any] = {
                "message": "Update IriSSave diagnostic snapshot",
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": GITHUB_DIAGNOSTICS_BRANCH,
            }
            if current_sha:
                body["sha"] = current_sha

            response = client.put(api_url, json=body)
            response.raise_for_status()

        print(
            "IRISSAVE DIAGNOSTICS GITHUB: snapshot обновлён",
            flush=True,
        )
        return True

    except Exception as error:
        print(
            "IRISSAVE DIAGNOSTICS GITHUB: ошибка публикации | "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
        return False

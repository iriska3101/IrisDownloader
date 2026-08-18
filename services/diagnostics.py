import os
import re
import sys
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import TextIO
from urllib.parse import urlsplit, urlunsplit


_MAX_LINES = 300
_LINES: deque[str] = deque(maxlen=_MAX_LINES)
_LOCK = Lock()
_ORIGINAL_STDOUT = sys.stdout
_ORIGINAL_STDERR = sys.stderr
_INSTALLED = False


def _sanitize_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    try:
        parts = urlsplit(raw)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return "[URL]"


def sanitize_line(text: str) -> str:
    value = text

    value = re.sub(
        r"https?://[^\s'\"<>]+",
        _sanitize_url,
        value,
    )

    value = re.sub(
        r"(?i)(bot_token|token|authorization|cookie|set-cookie|signature)\s*[=:]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        value,
    )

    value = re.sub(
        r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b",
        "[TELEGRAM_BOT_TOKEN_REDACTED]",
        value,
    )

    return value[:4000]


def record_line(text: str) -> None:
    cleaned = sanitize_line(text.strip())
    if not cleaned:
        return

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _LOCK:
        _LINES.append(f"{timestamp} {cleaned}")


def get_recent_logs(limit: int = 200) -> list[str]:
    safe_limit = max(1, min(limit, _MAX_LINES))
    with _LOCK:
        return list(_LINES)[-safe_limit:]


def get_debug_token() -> str | None:
    token = os.getenv("IRISSAVE_DEBUG_TOKEN", "").strip()
    return token or None


class _TeeStream:
    def __init__(self, original: TextIO) -> None:
        self.original = original
        self._buffer = ""

    def write(self, data: str) -> int:
        written = self.original.write(data)
        self.original.flush()

        self._buffer += data
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            record_line(line)

        return written

    def flush(self) -> None:
        self.original.flush()

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return getattr(self.original, "encoding", "utf-8")


def install_diagnostic_capture() -> None:
    global _INSTALLED

    if _INSTALLED:
        return

    sys.stdout = _TeeStream(_ORIGINAL_STDOUT)  # type: ignore[assignment]
    sys.stderr = _TeeStream(_ORIGINAL_STDERR)  # type: ignore[assignment]
    _INSTALLED = True
    record_line("IRISSAVE DIAGNOSTICS: capture enabled")

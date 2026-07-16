import re
from typing import Any


def find_link(text: str) -> str | None:
    """Находит первую ссылку в сообщении."""
    match = re.search(r"https?://\S+", text)

    if not match:
        return None

    return match.group(0).rstrip(".,)")


def clean_text(
    value: Any,
    fallback: str,
    max_length: int = 64,
) -> str:
    """Очищает текст и ограничивает его длину."""
    if not isinstance(value, str):
        return fallback

    cleaned = re.sub(r"\s+", " ", value).strip()

    if not cleaned:
        return fallback

    return cleaned[:max_length]

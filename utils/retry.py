import asyncio
from typing import Any, Callable

import httpx
from telegram.error import NetworkError, TimedOut


def is_temporary_error(error: Exception) -> bool:
    text = str(error).lower()

    temporary_words = (
        "timed out",
        "timeout",
        "temporarily",
        "connection reset",
        "connection error",
        "network",
        "remote end closed",
        "server disconnected",
        "502",
        "503",
        "504",
    )

    return (
        isinstance(
            error,
            (
                TimeoutError,
                TimedOut,
                NetworkError,
                httpx.TimeoutException,
                httpx.NetworkError,
            ),
        )
        or any(word in text for word in temporary_words)
    )


async def run_with_retry(
    operation: Callable[..., Any],
    *args: Any,
    status_message,
) -> Any:
    try:
        return await asyncio.to_thread(
            operation,
            *args,
        )

    except Exception as first_error:
        if not is_temporary_error(first_error):
            raise

        await status_message.edit_text(
            "Первая попытка не удалась.\n"
            "Повторяю ещё раз… 🔄"
        )

        await asyncio.sleep(3)

        return await asyncio.to_thread(
            operation,
            *args,
        )
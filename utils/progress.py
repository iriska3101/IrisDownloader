import asyncio
import time
from typing import Any

from telegram import Message


def format_bytes(value: float | int | None) -> str:
    """Красиво оформляет размер и скорость."""
    if not value:
        return "—"

    size = float(value)

    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024:
            return f"{size:.1f} {unit}"

        size /= 1024

    return f"{size:.1f} ТБ"


def progress_bar(percent: float) -> str:
    """Создаёт полоску прогресса из десяти делений."""
    safe_percent = max(0.0, min(percent, 100.0))
    filled = round(safe_percent / 10)

    return (
        "█" * filled
        + "░" * (10 - filled)
    )


class DownloadProgress:
    """Передаёт прогресс yt-dlp из потока в Telegram."""

    def __init__(
        self,
        message: Message,
        title: str,
    ) -> None:
        self.message = message
        self.title = title
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[dict[str, Any]] = (
            asyncio.Queue(maxsize=30)
        )
        self.last_percent = -1
        self.last_update_time = 0.0
        self.task: asyncio.Task[None] | None = None
        self.stopped = False

    def hook(
        self,
        data: dict[str, Any],
    ) -> None:
        """Эту функцию вызывает yt-dlp из рабочего потока."""
        copied_data = dict(data)

        def put_update() -> None:
            if self.stopped:
                return

            try:
                self.queue.put_nowait(copied_data)
            except asyncio.QueueFull:
                pass

        self.loop.call_soon_threadsafe(
            put_update
        )

    async def start(self) -> None:
        self.task = asyncio.create_task(
            self._update_message()
        )

    async def stop(self) -> None:
        self.stopped = True

        if self.task:
            self.task.cancel()

            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _update_message(self) -> None:
        while True:
            data = await self.queue.get()
            status = data.get("status")

            if status == "finished":
                await self._safe_edit(
                    f"{self.title}\n\n"
                    "██████████ 100%\n"
                    "⚙️ Обрабатываю файл…"
                )
                continue

            if status != "downloading":
                continue

            downloaded = data.get(
                "downloaded_bytes",
                0,
            )

            total = (
                data.get("total_bytes")
                or data.get(
                    "total_bytes_estimate"
                )
            )

            if total:
                percent = (
                    downloaded / total * 100
                )
            else:
                percent = 0.0

            now = time.monotonic()
            percent_integer = int(percent)

            changed_enough = (
                percent_integer
                >= self.last_percent + 5
            )

            enough_time_passed = (
                now - self.last_update_time
                >= 2
            )

            if (
                not changed_enough
                and not enough_time_passed
            ):
                continue

            self.last_percent = percent_integer
            self.last_update_time = now

            speed = format_bytes(
                data.get("speed")
            )

            eta = data.get("eta")
            eta_text = (
                f"{int(eta)} сек."
                if eta is not None
                else "—"
            )

            await self._safe_edit(
                f"{self.title}\n\n"
                f"{progress_bar(percent)} "
                f"{percent_integer}%\n"
                f"Скорость: {speed}/с\n"
                f"Осталось: {eta_text}"
            )

    async def _safe_edit(
        self,
        text: str,
    ) -> None:
        try:
            await self.message.edit_text(text)
        except Exception:
            # Пропускаем временные ошибки Telegram,
            # чтобы само скачивание не остановилось.
            pass
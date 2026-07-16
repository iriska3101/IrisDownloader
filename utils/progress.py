import asyncio
import time
from typing import Any

from telegram import Message


def format_bytes(
    value: float | int | None,
) -> str:
    """Красиво оформляет размер или скорость."""
    if not value:
        return "—"

    size = float(value)

    for unit in (
        "Б",
        "КБ",
        "МБ",
        "ГБ",
    ):
        if size < 1024:
            return f"{size:.1f} {unit}"

        size /= 1024

    return f"{size:.1f} ТБ"


def progress_bar(
    percent: float,
) -> str:
    """Создаёт полоску прогресса из десяти делений."""
    safe_percent = max(
        0.0,
        min(percent, 100.0),
    )

    filled = round(
        safe_percent / 10
    )

    return (
        "█" * filled
        + "░" * (10 - filled)
    )


class DownloadProgress:
    """Показывает в Telegram прогресс загрузки yt-dlp."""

    def __init__(
        self,
        message: Message,
        title: str,
    ) -> None:
        self.message = message
        self.title = title

        self.loop = (
            asyncio.get_running_loop()
        )

        self.latest_data: (
            dict[str, Any] | None
        ) = None

        self.update_event = asyncio.Event()

        self.last_percent = -1
        self.last_update_time = 0.0

        self.task: (
            asyncio.Task[None] | None
        ) = None

        self.stopped = False

    def hook(
        self,
        data: dict[str, Any],
    ) -> None:
        """Получает данные от yt-dlp из рабочего потока."""
        copied_data = dict(data)

        def save_update() -> None:
            if self.stopped:
                return

            self.latest_data = copied_data
            self.update_event.set()

        self.loop.call_soon_threadsafe(
            save_update
        )

    async def start(self) -> None:
        self.stopped = False

        self.task = asyncio.create_task(
            self._update_message()
        )

    async def stop(self) -> None:
        # Даём циклу событий обработать последнее
        # сообщение progress_hook от yt-dlp.
        await asyncio.sleep(0)

        if self.latest_data is not None:
            await self._render(
                self.latest_data,
                force=True,
            )

        self.stopped = True
        self.update_event.set()

        if self.task is not None:
            self.task.cancel()

            try:
                await self.task
            except asyncio.CancelledError:
                pass

            self.task = None

    async def _update_message(
        self,
    ) -> None:
        while not self.stopped:
            try:
                await asyncio.wait_for(
                    self.update_event.wait(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue

            self.update_event.clear()

            data = self.latest_data

            if data is None:
                continue

            await self._render(data)

    async def _render(
        self,
        data: dict[str, Any],
        force: bool = False,
    ) -> None:
        status = data.get("status")

        if status == "finished":
            await self._safe_edit(
                f"{self.title}\n\n"
                "██████████ 100%\n"
                "⚙️ Обрабатываю файл…"
            )
            return

        if status != "downloading":
            return

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

        percent_integer = int(percent)
        now = time.monotonic()

        changed_enough = (
            percent_integer
            >= self.last_percent + 5
        )

        enough_time_passed = (
            now - self.last_update_time
            >= 1.2
        )

        if (
            not force
            and not changed_enough
            and not enough_time_passed
        ):
            return

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

        if total:
            progress_text = (
                f"{progress_bar(percent)} "
                f"{percent_integer}%"
            )
        else:
            downloaded_text = (
                format_bytes(downloaded)
            )

            progress_text = (
                "⬇️ Загружено: "
                f"{downloaded_text}"
            )

        await self._safe_edit(
            f"{self.title}\n\n"
            f"{progress_text}\n"
            f"⚡ Скорость: {speed}/с\n"
            f"⏳ Осталось: {eta_text}"
        )

    async def _safe_edit(
        self,
        text: str,
    ) -> None:
        try:
            await self.message.edit_text(
                text
            )
        except Exception:
            # Ошибка обновления статуса не должна
            # прерывать само скачивание.
            pass

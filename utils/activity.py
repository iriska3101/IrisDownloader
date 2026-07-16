import asyncio

from telegram import Message


class ActivityIndicator:
    """Показывает живую анимацию, пока выполняется операция."""

    FRAMES = (
        "◐",
        "◓",
        "◑",
        "◒",
    )

    def __init__(
        self,
        message: Message,
        text: str,
    ) -> None:
        self.message = message
        self.text = text
        self.task: asyncio.Task[None] | None = None
        self.stopped = False

    async def start(self) -> None:
        self.stopped = False
        self.task = asyncio.create_task(
            self._animate()
        )

    async def stop(self) -> None:
        self.stopped = True

        if self.task is None:
            return

        self.task.cancel()

        try:
            await self.task
        except asyncio.CancelledError:
            pass

        self.task = None

    async def change_text(
        self,
        text: str,
    ) -> None:
        self.text = text

    async def _animate(self) -> None:
        frame_index = 0

        while not self.stopped:
            frame = self.FRAMES[
                frame_index % len(self.FRAMES)
            ]

            await self._safe_edit(
                "🌸 IriSSave\n\n"
                f"{frame} {self.text}"
            )

            frame_index += 1
            await asyncio.sleep(1.2)

    async def _safe_edit(
        self,
        text: str,
    ) -> None:
        try:
            await self.message.edit_text(text)
        except Exception:
            # Временная ошибка Telegram не должна
            # останавливать само скачивание.
            pass
import asyncio
import hmac

import tornado.web
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import (
    BOT_TOKEN,
    PORT,
    WEBHOOK_PATH,
    WEBHOOK_URL,
)
from handlers.callbacks import (
    handle_download_choice,
    handle_search_choice,
)
from handlers.messages import start
from handlers.text import handle_text
from services.diagnostics import (
    get_debug_token,
    get_public_logs,
    get_recent_logs,
    install_diagnostic_capture,
    record_line,
)


class DebugLogsHandler(tornado.web.RequestHandler):
    """Отдаёт последние безопасно очищенные логи IriSSave по токену."""

    def set_default_headers(self) -> None:
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.set_header("Cache-Control", "no-store")

    def get(self) -> None:
        expected = get_debug_token()

        if not expected:
            self.set_status(503)
            self.finish(
                {
                    "ok": False,
                    "error": "IRISSAVE_DEBUG_TOKEN is not configured",
                }
            )
            return

        supplied = self.get_query_argument("token", default="")

        if not supplied:
            authorization = self.request.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                supplied = authorization[7:].strip()

        if not supplied or not hmac.compare_digest(supplied, expected):
            self.set_status(404)
            self.finish({"ok": False})
            return

        try:
            limit = int(self.get_query_argument("limit", default="200"))
        except ValueError:
            limit = 200

        self.finish(
            {
                "ok": True,
                "lines": get_recent_logs(limit),
            }
        )


class PublicDebugHandler(tornado.web.RequestHandler):
    """
    Публичная диагностика без токена.

    Здесь выдаются только allowlist-технические строки после усиленной
    очистки: без URL, media ID, локальных путей, cookies и токенов.
    """

    def set_default_headers(self) -> None:
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.set_header("Cache-Control", "no-store")
        self.set_header("X-Robots-Tag", "noindex, nofollow, noarchive")

    def get(self) -> None:
        try:
            limit = int(self.get_query_argument("limit", default="120"))
        except ValueError:
            limit = 120

        self.finish(
            {
                "ok": True,
                "service": "IriSSave",
                "diagnostics": "public-safe",
                "lines": get_public_logs(limit),
            }
        )


async def _attach_debug_route(application: Application) -> None:
    """
    Добавляет diagnostic routes в тот же Tornado-сервер, который PTB уже
    использует для Telegram webhook. Поэтому второй порт Render не нужен.
    """
    updater = application.updater
    if updater is None:
        record_line("IRISSAVE DIAGNOSTICS: updater недоступен")
        return

    for _ in range(100):
        httpd = getattr(updater, "_httpd", None)
        http_server = getattr(httpd, "_http_server", None)
        web_app = getattr(http_server, "request_callback", None)

        if web_app is not None and hasattr(web_app, "add_handlers"):
            web_app.add_handlers(
                r".*$",
                [
                    (r"/debug/logs/?", DebugLogsHandler),
                    (r"/debug/status/?", PublicDebugHandler),
                ],
            )
            record_line(
                "IRISSAVE DIAGNOSTICS: /debug/logs and /debug/status attached"
            )
            return

        await asyncio.sleep(0.1)

    record_line("IRISSAVE DIAGNOSTICS: не удалось подключить debug routes")


async def _post_init(application: Application) -> None:
    asyncio.create_task(
        _attach_debug_route(application),
        name="irissave-debug-route",
    )


def main() -> None:
    install_diagnostic_capture()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            handle_download_choice,
            pattern=r"^download_(video|audio|photos)$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            handle_search_choice,
            pattern=r"^search_audio:[a-f0-9]{10}:\d+$",
        )
    )

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=WEBHOOK_URL,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()

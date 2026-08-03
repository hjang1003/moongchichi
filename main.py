from __future__ import annotations
import logging

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import config
from handlers.onboarding import get_onboarding_handler
from handlers.briefing import cmd_briefing, cmd_schedule, cmd_recap, cmd_term
from handlers.admin import cmd_reset
from handlers.general import (
    cmd_pause, cmd_resume, cmd_alarm,
    cmd_profile, cmd_update, cmd_request, cmd_feedback, cmd_help,
    cmd_distribution,
    handle_natural_language,
)
from handlers.callbacks import handle_notion_save, handle_notion_delete, handle_source_view
from scheduler import setup_scheduler
from services.sheets import get_sheets
from services.notion import get_notion

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(app: Application) -> None:
    """Called inside the event loop after the app is initialized."""
    sheets = get_sheets()
    try:
        alarm_time = await sheets.get_setting("알람_시간")
        alarm_time = alarm_time if alarm_time else "08:00"
    except Exception:
        alarm_time = "08:00"

    try:
        notion = get_notion()
        await notion.ensure_databases(sheets)
    except Exception as e:
        logger.warning("Notion DB auto-setup failed (non-fatal): %s", e)

    setup_scheduler(app, alarm_time)
    logger.info("Moongchichi bot ready. Daily briefing at %s KST (weekdays).", alarm_time)


def main() -> None:
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Onboarding conversation (handles /start and auth flow)
    app.add_handler(get_onboarding_handler())

    # Briefing commands
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("recap", cmd_recap))
    app.add_handler(CommandHandler("term", cmd_term))

    # Admin
    app.add_handler(CommandHandler("reset", cmd_reset))

    # General commands
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("alarm", cmd_alarm))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CommandHandler("request", cmd_request))
    app.add_handler(CommandHandler("feedback", cmd_feedback))
    app.add_handler(CommandHandler("distribution", cmd_distribution))
    app.add_handler(CommandHandler("help", cmd_help))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(handle_notion_save, pattern=r"^notion_save:"))
    app.add_handler(CallbackQueryHandler(handle_notion_delete, pattern=r"^notion_delete:"))
    app.add_handler(CallbackQueryHandler(handle_source_view, pattern=r"^source_view:"))

    # Natural language fallback
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_natural_language))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

from __future__ import annotations
import asyncio
import logging

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

import config
from handlers.onboarding import get_onboarding_handler
from handlers.briefing import cmd_briefing, cmd_today, cmd_recap, cmd_topic, cmd_term
from handlers.notion_cmds import cmd_save, cmd_source
from handlers.admin import cmd_reset
from handlers.general import (
    cmd_status, cmd_pause, cmd_resume, cmd_alarm,
    cmd_profile, cmd_history, cmd_feedback, cmd_help,
    handle_natural_language,
)
from scheduler import setup_scheduler
from services.sheets import get_sheets

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def _read_alarm_time() -> str:
    try:
        sheets = get_sheets()
        t = await sheets.get_setting("알람_시간")
        return t if t else "08:00"
    except Exception:
        return "08:00"


def main() -> None:
    alarm_time = asyncio.run(_read_alarm_time())

    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Onboarding conversation (handles /start and auth flow)
    app.add_handler(get_onboarding_handler())

    # Briefing commands
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("recap", cmd_recap))
    app.add_handler(CommandHandler("topic", cmd_topic))
    app.add_handler(CommandHandler("term", cmd_term))

    # Notion commands
    app.add_handler(CommandHandler("save", cmd_save))
    app.add_handler(CommandHandler("source", cmd_source))

    # Admin
    app.add_handler(CommandHandler("reset", cmd_reset))

    # General commands
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("alarm", cmd_alarm))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("feedback", cmd_feedback))
    app.add_handler(CommandHandler("help", cmd_help))

    # Natural language fallback
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_natural_language))

    # Setup daily scheduler with alarm time from sheets
    setup_scheduler(app, alarm_time)

    logger.info("Moongchichi bot starting... (alarm: %s KST)", alarm_time)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

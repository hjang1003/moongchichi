from __future__ import annotations
import logging
import datetime

import pytz
from telegram.ext import Application

import config
import utils
from services.sheets import get_sheets
from services.notion import get_notion
from services.claude import get_claude
from services.briefing import create_and_send_briefing

logger = logging.getLogger(__name__)

KOREA_TZ = pytz.timezone(config.KOREA_TZ)


async def _send_daily_briefing(context) -> None:
    sheets = get_sheets()

    if not await sheets.is_active():
        logger.info("Bot is paused, skipping daily briefing.")
        return

    now = utils.get_korea_now()
    if not utils.is_weekday(now):
        return

    user_id = await sheets.get_setting("사용자_ChatID")
    admin_id = await sheets.get_setting("관리자_ChatID")
    targets = [uid for uid in [user_id, admin_id] if uid]

    for uid in targets:
        try:
            await create_and_send_briefing(context, int(uid))
            logger.info("Daily briefing sent to %s", uid)
        except Exception as e:
            logger.error("Failed to send daily briefing to %s: %s", uid, e)


async def _check_monthly_summary(context) -> None:
    now = utils.get_korea_now()
    if not utils.is_last_weekday_of_month(now):
        return

    sheets = get_sheets()
    if not await sheets.is_active():
        return

    notion = get_notion()
    claude = get_claude()

    saved_briefings = await notion.get_saved_briefings_this_month(now.year, now.month)
    if not saved_briefings:
        rows = await sheets.get_saved_history_this_month(now.year, now.month)
        saved_briefings = [
            {"date": r.get("날짜", ""), "theme": r.get("요일테마", ""), "content": ""}
            for r in rows
        ]

    try:
        summary = await claude.generate_monthly_summary(saved_briefings)
    except Exception as e:
        logger.error("Monthly summary generation failed: %s", e)
        return

    user_id = await sheets.get_setting("사용자_ChatID")
    admin_id = await sheets.get_setting("관리자_ChatID")
    targets = [uid for uid in [user_id, admin_id] if uid]

    parts = utils.split_message(summary)
    for uid in targets:
        try:
            for part in parts:
                await context.bot.send_message(chat_id=int(uid), text=part)
            logger.info("Monthly summary sent to %s", uid)
        except Exception as e:
            logger.error("Failed to send monthly summary to %s: %s", uid, e)


def setup_scheduler(app: Application, alarm_time_str: str = "08:00") -> None:
    hour, minute = map(int, alarm_time_str.split(":"))

    briefing_time = datetime.time(hour=hour, minute=minute, tzinfo=KOREA_TZ)

    summary_minute = minute + 1 if minute < 59 else 0
    summary_hour = hour if minute < 59 else (hour + 1) % 24
    summary_time = datetime.time(hour=summary_hour, minute=summary_minute, tzinfo=KOREA_TZ)

    app.job_queue.run_daily(
        _send_daily_briefing,
        time=briefing_time,
        days=(0, 1, 2, 3, 4),
        name="daily_briefing",
    )

    app.job_queue.run_daily(
        _check_monthly_summary,
        time=summary_time,
        days=(0, 1, 2, 3, 4),
        name="monthly_summary",
    )

    logger.info(
        "Scheduler ready: daily briefing at %02d:%02d KST (weekdays only)",
        hour,
        minute,
    )

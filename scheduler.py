from __future__ import annotations
import logging
from datetime import date

import holidays
from telegram.ext import Application

import utils
from services.sheets import get_sheets
from services.notion import get_notion
from services.claude import get_claude
from services.briefing import create_and_send_briefing

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
WEEKDAYS = (0, 1, 2, 3, 4)

_KR_HOLIDAYS = holidays.country_holidays("KR")

# 패키지가 모르는 임시공휴일이 생기면 여기에 추가
MANUAL_KR_HOLIDAYS: set[date] = set()


def is_korean_holiday(d: date) -> bool:
    return d in _KR_HOLIDAYS or d in MANUAL_KR_HOLIDAYS


async def _notify_admin_failure(
    context,
    admin_id,
    date_str: str,
    stage: str,
    error_msg: str,
    header: str = "⚠️ 일일 브리핑 발송 실패",
) -> None:
    if not admin_id:
        return
    try:
        admin_int = int(str(admin_id).strip())
        text = (
            f"{header}\n\n"
            f"날짜: {date_str}\n"
            f"단계: {stage}\n"
            f"오류: {error_msg}"
        )
        await context.bot.send_message(chat_id=admin_int, text=text)
    except Exception as notify_err:
        logger.error("Failed to notify admin of briefing failure: %s", notify_err)


async def _send_daily_briefing(context) -> None:
    now = utils.get_korea_now()
    if now.weekday() not in WEEKDAYS:
        return

    if is_korean_holiday(now.date()):
        today_str = now.strftime("%Y-%m-%d")
        if context.bot_data.get("last_briefing_date") != today_str:
            logger.info("Today (%s) is a Korean holiday, skipping daily briefing.", today_str)
            context.bot_data["last_briefing_date"] = today_str
        return

    target_hour, target_minute = context.job.data
    if now.hour != target_hour or now.minute != target_minute:
        return

    today_str = now.strftime("%Y-%m-%d")
    if context.bot_data.get("last_briefing_date") == today_str:
        return

    sheets = get_sheets()
    if not await sheets.is_active():
        logger.info("Bot is paused, skipping daily briefing.")
        return

    context.bot_data["last_briefing_date"] = today_str

    user_id = await sheets.get_setting("사용자_ChatID")
    admin_id = await sheets.get_setting("관리자_ChatID")

    primary = None
    mirrors = []
    if user_id and str(user_id).strip():
        try:
            primary = int(str(user_id).strip())
        except ValueError:
            logger.warning("Invalid 사용자_ChatID: %s", user_id)

    if admin_id and str(admin_id).strip():
        try:
            admin_int = int(str(admin_id).strip())
            if primary is None:
                primary = admin_int
            elif admin_int != primary:
                mirrors.append(admin_int)
        except ValueError:
            logger.warning("Invalid 관리자_ChatID: %s", admin_id)

    if primary is None:
        logger.warning("No targets configured for daily briefing.")
        return

    try:
        from services.briefing import create_and_send_briefing_to_many
        successes, failures = await create_and_send_briefing_to_many(context, primary, mirrors)

        if failures:
            user_failed = primary in failures
            mirror_failures = [f for f in failures if f != primary]

            detail_lines = []
            if user_failed:
                detail_lines.append(f"- 사용자 ({primary})")
            for m in mirror_failures:
                detail_lines.append(f"- 관리자 미러 ({m})")

            success_str = ", ".join(str(s) for s in successes) if successes else "없음"
            error_msg = "실패 대상:\n" + "\n".join(detail_lines) + f"\n성공 대상: {success_str}"
            header = "🚨 사용자 발송 실패!" if user_failed else "⚠️ 일일 브리핑 발송 실패 (미러)"

            logger.error(
                "Daily briefing had failures: successes=%s failures=%s user_failed=%s",
                successes, failures, user_failed,
            )
            await _notify_admin_failure(
                context, admin_id, today_str,
                stage="수신자별 발송",
                error_msg=error_msg,
                header=header,
            )
        elif successes:
            logger.info("Daily briefing sent (primary=%s, mirrors=%s)", primary, mirrors)
        else:
            logger.info("Daily briefing skipped (no theme or no recipients).")
    except Exception as e:
        logger.error("Failed to send daily briefing: %s", e)
        await _notify_admin_failure(
            context, admin_id, today_str,
            stage="일일 브리핑 실행 중 예외",
            error_msg=f"{type(e).__name__}: {e}",
        )


async def _check_monthly_summary(context) -> None:
    now = utils.get_korea_now()

    if now.weekday() not in WEEKDAYS:
        return

    target_hour, target_minute = context.job.data
    if now.hour != target_hour or now.minute != target_minute:
        return

    if not utils.is_last_weekday_of_month(now):
        return

    today_str = now.strftime("%Y-%m-%d")
    if context.bot_data.get("last_monthly_summary_date") == today_str:
        return

    sheets = get_sheets()
    if not await sheets.is_active():
        return

    context.bot_data["last_monthly_summary_date"] = today_str

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


def reschedule_jobs(job_queue, alarm_time_str: str) -> None:
    hour, minute = map(int, alarm_time_str.split(":"))

    summary_minute = minute + 1 if minute < 59 else 0
    summary_hour = hour if minute < 59 else (hour + 1) % 24

    for job_name in ("daily_briefing", "monthly_summary"):
        for job in job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()

    job_queue.run_repeating(
        _send_daily_briefing,
        interval=POLL_INTERVAL_SECONDS,
        first=0,
        data=(hour, minute),
        name="daily_briefing",
    )

    job_queue.run_repeating(
        _check_monthly_summary,
        interval=POLL_INTERVAL_SECONDS,
        first=0,
        data=(summary_hour, summary_minute),
        name="monthly_summary",
    )

    logger.info(
        "Scheduler ready: daily briefing at %02d:%02d KST (weekdays only), polling every %ds",
        hour,
        minute,
        POLL_INTERVAL_SECONDS,
    )


def setup_scheduler(app: Application, alarm_time_str: str = "08:00") -> None:
    reschedule_jobs(app.job_queue, alarm_time_str)

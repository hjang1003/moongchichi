from __future__ import annotations
import functools
import logging
from datetime import date

from telegram.ext import Application

import utils
from services.sheets import get_sheets
from services.notion import get_notion
from services.claude import get_claude
from services.briefing import create_and_send_briefing, strip_markdown

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
WEEKDAYS = (0, 1, 2, 3, 4)

# 한국 공휴일. holidays 패키지(0.101)가 계산한 날짜를 그대로 옮겨 적었다.
# 패키지를 불러오면 다른 나라 달력 모듈 300개 가까이가 함께 올라와 메모리를 약 11MB 더 쓴다.
# 이 범위 밖의 해만 패키지로 계산한다. 범위를 늘릴 때는 같은 패키지로 다시 뽑아 붙인다.
_KR_HOLIDAYS_YEARS = range(2026, 2031)
_KR_HOLIDAYS: set[date] = {
    # 2026
    date(2026, 1, 1),  # 신정연휴
    date(2026, 2, 16),  # 설날 전날
    date(2026, 2, 17),  # 설날
    date(2026, 2, 18),  # 설날 다음날
    date(2026, 3, 1),  # 삼일절
    date(2026, 3, 2),  # 삼일절 대체 휴일
    date(2026, 5, 5),  # 어린이날
    date(2026, 5, 24),  # 부처님오신날
    date(2026, 5, 25),  # 부처님오신날 대체 휴일
    date(2026, 6, 3),  # 지방선거일
    date(2026, 6, 6),  # 현충일
    date(2026, 7, 17),  # 제헌절
    date(2026, 8, 15),  # 광복절
    date(2026, 8, 17),  # 광복절 대체 휴일
    date(2026, 9, 24),  # 추석 전날
    date(2026, 9, 25),  # 추석
    date(2026, 9, 26),  # 추석 다음날
    date(2026, 10, 3),  # 개천절
    date(2026, 10, 5),  # 개천절 대체 휴일
    date(2026, 10, 9),  # 한글날
    date(2026, 12, 25),  # 기독탄신일
    # 2027
    date(2027, 1, 1),  # 신정연휴
    date(2027, 2, 6),  # 설날 전날
    date(2027, 2, 7),  # 설날
    date(2027, 2, 8),  # 설날 다음날
    date(2027, 2, 9),  # 설날 대체 휴일
    date(2027, 3, 1),  # 삼일절
    date(2027, 5, 5),  # 어린이날
    date(2027, 5, 13),  # 부처님오신날
    date(2027, 6, 6),  # 현충일
    date(2027, 7, 17),  # 제헌절
    date(2027, 8, 15),  # 광복절
    date(2027, 8, 16),  # 광복절 대체 휴일
    date(2027, 9, 14),  # 추석 전날
    date(2027, 9, 15),  # 추석
    date(2027, 9, 16),  # 추석 다음날
    date(2027, 10, 3),  # 개천절
    date(2027, 10, 4),  # 개천절 대체 휴일
    date(2027, 10, 9),  # 한글날
    date(2027, 10, 11),  # 한글날 대체 휴일
    date(2027, 12, 25),  # 기독탄신일
    date(2027, 12, 27),  # 기독탄신일 대체 휴일
    # 2028
    date(2028, 1, 1),  # 신정연휴
    date(2028, 1, 26),  # 설날 전날
    date(2028, 1, 27),  # 설날
    date(2028, 1, 28),  # 설날 다음날
    date(2028, 3, 1),  # 삼일절
    date(2028, 4, 12),  # 국회의원 선거일
    date(2028, 5, 2),  # 부처님오신날
    date(2028, 5, 5),  # 어린이날
    date(2028, 6, 6),  # 현충일
    date(2028, 7, 17),  # 제헌절
    date(2028, 8, 15),  # 광복절
    date(2028, 10, 2),  # 추석 전날
    date(2028, 10, 3),  # 개천절; 추석
    date(2028, 10, 4),  # 추석 다음날
    date(2028, 10, 5),  # 추석 대체 휴일
    date(2028, 10, 9),  # 한글날
    date(2028, 12, 25),  # 기독탄신일
    # 2029
    date(2029, 1, 1),  # 신정연휴
    date(2029, 2, 12),  # 설날 전날
    date(2029, 2, 13),  # 설날
    date(2029, 2, 14),  # 설날 다음날
    date(2029, 3, 1),  # 삼일절
    date(2029, 5, 5),  # 어린이날
    date(2029, 5, 7),  # 어린이날 대체 휴일
    date(2029, 5, 20),  # 부처님오신날
    date(2029, 5, 21),  # 부처님오신날 대체 휴일
    date(2029, 6, 6),  # 현충일
    date(2029, 7, 17),  # 제헌절
    date(2029, 8, 15),  # 광복절
    date(2029, 9, 21),  # 추석 전날
    date(2029, 9, 22),  # 추석
    date(2029, 9, 23),  # 추석 다음날
    date(2029, 9, 24),  # 추석 대체 휴일
    date(2029, 10, 3),  # 개천절
    date(2029, 10, 9),  # 한글날
    date(2029, 12, 25),  # 기독탄신일
    # 2030
    date(2030, 1, 1),  # 신정연휴
    date(2030, 2, 2),  # 설날 전날
    date(2030, 2, 3),  # 설날
    date(2030, 2, 4),  # 설날 다음날
    date(2030, 2, 5),  # 설날 대체 휴일
    date(2030, 3, 1),  # 삼일절
    date(2030, 4, 3),  # 대통령 선거일
    date(2030, 5, 5),  # 어린이날
    date(2030, 5, 6),  # 어린이날 대체 휴일
    date(2030, 5, 9),  # 부처님오신날
    date(2030, 6, 6),  # 현충일
    date(2030, 6, 12),  # 지방선거일
    date(2030, 7, 17),  # 제헌절
    date(2030, 8, 15),  # 광복절
    date(2030, 9, 11),  # 추석 전날
    date(2030, 9, 12),  # 추석
    date(2030, 9, 13),  # 추석 다음날
    date(2030, 10, 3),  # 개천절
    date(2030, 10, 9),  # 한글날
    date(2030, 12, 25),  # 기독탄신일
}

# 패키지가 모르는 임시공휴일이 생기면 여기에 추가
MANUAL_KR_HOLIDAYS: set[date] = {date(2026, 10, 7), date(2026, 10, 8)}  # 2026-10 둘째 주 휴무


@functools.cache
def _kr_holidays_from_package():
    import holidays
    return holidays.country_holidays("KR")


def is_korean_holiday(d: date) -> bool:
    kr_holidays = _KR_HOLIDAYS if d.year in _KR_HOLIDAYS_YEARS else _kr_holidays_from_package()
    return d in kr_holidays or d in MANUAL_KR_HOLIDAYS


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

    parts = utils.split_message(strip_markdown(summary))
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

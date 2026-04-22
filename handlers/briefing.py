from __future__ import annotations
import logging

from telegram import Update
from telegram.ext import ContextTypes

from handlers.auth import require_auth
from services.sheets import get_sheets
from services.notion import get_notion
from services.claude import get_claude
from services.briefing import create_and_send_briefing
import utils
import config

logger = logging.getLogger(__name__)


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    chat_id = update.effective_chat.id
    await update.message.reply_text("📬 브리핑을 가져오는 중이에요... 잠깐만요!")
    try:
        success = await create_and_send_briefing(context, chat_id)
    except Exception as e:
        logger.error("Unhandled error in create_and_send_briefing: %s", e)
        success = False
    if not success:
        await update.message.reply_text("일시적인 오류가 발생했어요 😥 잠시 후 다시 시도해 주세요!")


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    now = utils.get_korea_now()
    weekday_ko = config.WEEKDAY_KO.get(now.weekday(), "")
    theme = utils.get_weekday_theme(now)
    if not theme:
        await update.message.reply_text("오늘은 주말이라 브리핑이 없어요 😊\n주중에 만나요!")
        return
    date_display = utils.format_date_korean(now)
    await update.message.reply_text(
        f"📅 오늘의 브리핑 테마\n\n"
        f"{date_display}\n"
        f"📌 {theme}\n\n"
        f"오늘의 브리핑을 바로 받으려면 /briefing 을 눌러 주세요!"
    )


async def cmd_recap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "날짜 또는 표현을 입력해 주세요!\n예: /recap 2025-04-01\n예: /recap 저번 주 금요일"
        )
        return

    query = " ".join(args)
    now = utils.get_korea_now()
    today_str = utils.date_to_str(now)

    # Try to parse date
    claude = get_claude()
    sheets = get_sheets()

    try:
        date_str = await claude.parse_date_expression(query, today_str)
        # Validate format YYYY-MM-DD
        import re
        if not re.match(r"\d{4}-\d{2}-\d{2}", date_str):
            raise ValueError("Invalid date format")
        date_str = date_str[:10]
    except Exception:
        await update.message.reply_text(
            "날짜를 이해하지 못했어요 😥\n다음 형식으로 입력해 주세요: /recap 2025-04-01"
        )
        return

    history_row = await sheets.get_history_by_date(date_str)
    if not history_row:
        await update.message.reply_text(
            f"📭 {date_str} 날짜의 브리핑 이력이 없어요.\n다른 날짜를 시도해 보세요!"
        )
        return

    theme = history_row.get("요일테마", "")
    sources = history_row.get("소스링크", "")
    await update.message.reply_text(
        f"📅 {date_str} 브리핑 요약\n\n"
        f"테마: {theme}\n"
        f"발송여부: {history_row.get('발송여부', '')}\n"
        f"노션저장: {history_row.get('노션저장여부', '')}\n\n"
        f"📎 소스 링크:\n{sources or '없음'}"
    )


async def cmd_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "주제를 입력해 주세요!\n예: /topic 인플루언서 마케팅"
        )
        return

    topic = " ".join(args)
    claude = get_claude()

    # Check if marketing-related (cheap model)
    is_marketing = await claude.is_marketing_topic(topic)
    if not is_marketing:
        await update.message.reply_text(
            "마케팅 관련 주제만 요청할 수 있어요 😊\n다른 주제를 시도해 보세요!"
        )
        return

    await update.message.reply_text(f"📌 {topic} 브리핑을 작성 중이에요...")
    sheets = get_sheets()
    profile = await sheets.get_profile()
    try:
        briefing = await claude.generate_topic_briefing(topic, profile)
    except Exception as e:
        logger.error("Topic briefing failed: %s", e)
        await update.message.reply_text("일시적인 오류가 발생했어요 😥 잠시 후 다시 시도해 주세요!")
        return

    parts = utils.split_message(briefing)
    for part in parts:
        await update.message.reply_text(part)


async def cmd_term(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "용어를 입력해 주세요!\n예: /term ROAS"
        )
        return

    term = " ".join(args)
    claude = get_claude()
    sheets = get_sheets()
    profile = await sheets.get_profile()

    try:
        explanation = await claude.explain_term(term, profile)
    except Exception as e:
        logger.error("Term explanation failed: %s", e)
        await update.message.reply_text("일시적인 오류가 발생했어요 😥 잠시 후 다시 시도해 주세요!")
        return

    parts = utils.split_message(explanation)
    for part in parts:
        await update.message.reply_text(part)

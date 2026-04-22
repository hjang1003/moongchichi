from __future__ import annotations
import logging

from telegram import Update
from telegram.ext import ContextTypes

from services.sheets import get_sheets
from services.notion import get_notion

logger = logging.getLogger(__name__)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    sheets = get_sheets()

    if not await sheets.is_admin(chat_id):
        await update.message.reply_text("🚫 관리자만 사용할 수 있는 명령어예요.")
        return

    await update.message.reply_text(
        "⚠️ 정말로 초기화할까요?\n\n"
        "초기화되는 항목:\n"
        "• 프로필 탭\n"
        "• 브리핑이력 탭\n"
        "• 사용자 Chat ID\n"
        "• 노션 마케팅 브리핑 DB\n"
        "• 노션 저장된 브리핑 DB\n\n"
        "확인하려면 '초기화확인' 을 입력해 주세요."
    )
    context.user_data["pending_reset"] = True


async def handle_reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    sheets = get_sheets()

    if not await sheets.is_admin(chat_id):
        return

    if not context.user_data.get("pending_reset"):
        return

    text = update.message.text.strip()
    if text != "초기화확인":
        context.user_data.pop("pending_reset", None)
        await update.message.reply_text("초기화를 취소했어요.")
        return

    context.user_data.pop("pending_reset", None)
    await update.message.reply_text("🔄 초기화 중이에요... 잠깐만요!")

    try:
        notion = get_notion()
        await sheets.reset_all()
        await notion.reset_briefing_db()
        await notion.reset_saved_db()
        await update.message.reply_text("✅ 초기화 완료! 봇을 새로 시작할 수 있어요.")
    except Exception as e:
        logger.error("Reset failed: %s", e)
        await update.message.reply_text("일시적인 오류가 발생했어요 😥 잠시 후 다시 시도해 주세요!")

from __future__ import annotations
import logging

from telegram import Update
from telegram.ext import ContextTypes

from handlers.auth import require_auth
from services.sheets import get_sheets
from services.notion import get_notion
import utils

logger = logging.getLogger(__name__)


async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return

    replied = update.message.reply_to_message
    if not replied:
        await update.message.reply_text(
            "저장할 브리핑 메시지를 답장(Reply)하면서 /save 를 입력해 주세요!"
        )
        return

    content = replied.text or ""
    if not content:
        await update.message.reply_text("텍스트 메시지만 저장할 수 있어요!")
        return

    now = utils.get_korea_now()
    date_str = utils.date_to_str(now)
    theme = utils.get_weekday_theme(now) or "기타"

    notion = get_notion()
    sheets = get_sheets()

    page_id = await notion.save_to_saved_db(date_str, theme, content)
    if page_id:
        await sheets.update_notion_saved(date_str)
        await update.message.reply_text(
            "⭐ 노션 [저장된 브리핑]에 저장했어요!\n나중에 노션에서 메모도 추가해 보세요 😊"
        )
    else:
        await update.message.reply_text(
            "일시적인 오류가 발생했어요 😥 잠시 후 다시 시도해 주세요!"
        )


async def cmd_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return

    replied = update.message.reply_to_message
    if not replied:
        # Fall back to last briefing in history
        sheets = get_sheets()
        history = await sheets.get_history(limit=1)
        if not history:
            await update.message.reply_text(
                "소스를 보려면 브리핑 메시지를 답장(Reply)하면서 /source 를 입력해 주세요!"
            )
            return
        last = history[-1]
        sources_str = last.get("소스링크", "")
        sources = [s for s in sources_str.split("|") if s.strip()]
        if sources:
            formatted = "\n".join([f"• {s}" for s in sources])
            await update.message.reply_text(
                f"📎 최근 브리핑 ({last.get('날짜', '')}) 소스 링크\n\n{formatted}"
            )
        else:
            await update.message.reply_text("저장된 소스 링크가 없어요.")
        return

    # Extract sources from the replied message
    content = replied.text or ""
    from services.briefing import extract_sources
    sources = extract_sources(content)

    if sources:
        formatted = "\n".join([f"• {s}" for s in sources])
        await update.message.reply_text(f"📎 원본 소스 링크\n\n{formatted}")
    else:
        await update.message.reply_text(
            "소스 링크를 찾지 못했어요. 브리핑 메시지에 출처가 포함되어 있지 않을 수 있어요."
        )

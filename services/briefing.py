from __future__ import annotations
import logging
from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from services.sheets import get_sheets
from services.notion import get_notion
from services.claude import get_claude
import utils
import config

logger = logging.getLogger(__name__)


def make_briefing_keyboard(date_str: str, saved: bool = False) -> InlineKeyboardMarkup:
    save_btn = (
        InlineKeyboardButton("🗑️ 노션에서 삭제", callback_data=f"notion_delete:{date_str}")
        if saved
        else InlineKeyboardButton("💾 노션에 저장", callback_data=f"notion_save:{date_str}")
    )
    source_btn = InlineKeyboardButton("📎 소스 보기", callback_data=f"source_view:{date_str}")
    return InlineKeyboardMarkup([[save_btn, source_btn]])


async def create_and_send_briefing(context, chat_id: int) -> bool:
    """Generate briefing, send to chat_id, save to sheets + notion. Returns True on success."""
    sheets = get_sheets()
    notion = get_notion()
    claude = get_claude()

    now = utils.get_korea_now()
    date_str = utils.date_to_str(now)
    weekday_ko = config.WEEKDAY_KO.get(now.weekday(), "")
    theme = utils.get_weekday_theme(now)

    if not theme:
        logger.info("Today (%s) is not a weekday, skipping briefing.", date_str)
        return False

    profile = await sheets.get_profile()

    try:
        briefing_text = await claude.generate_briefing(profile, theme, date_str, weekday_ko)
    except Exception as e:
        logger.error("Failed to generate briefing: %s", e)
        return False

    sources = extract_sources(briefing_text)
    parts = utils.split_message(briefing_text)

    # Send all parts except the last without a keyboard
    for part in parts[:-1]:
        try:
            await context.bot.send_message(chat_id=chat_id, text=part)
        except Exception as e:
            logger.error("Failed to send briefing part: %s", e)
            return False

    # Send the last part with inline buttons
    keyboard = make_briefing_keyboard(date_str)
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=parts[-1],
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error("Failed to send final briefing part: %s", e)
        return False

    # Cache briefing data in bot_data for button callbacks
    context.bot_data[f"briefing:{date_str}"] = {
        "content": briefing_text,
        "theme": theme,
        "sources": sources,
        "saved_page_id": None,
    }

    # Auto-save to Notion briefing DB
    notion_saved = False
    try:
        page_id = await notion.save_to_briefing_db(date_str, theme, briefing_text, sources)
        notion_saved = page_id is not None
    except Exception as e:
        logger.error("Notion briefing DB save failed (non-fatal): %s", e)

    try:
        await sheets.add_history(date_str, theme, True, sources, notion_saved)
    except Exception as e:
        logger.error("Sheets history save failed: %s", e)

    return True


def extract_sources(text: str) -> List[str]:
    lines = text.split("\n")
    sources = []
    in_source_section = False
    for line in lines:
        line = line.strip()
        if "참고 출처" in line:
            in_source_section = True
            continue
        if in_source_section:
            if line.startswith("※") or (line.startswith("━") and len(line) > 5):
                break
            if line and not line.startswith("━") and not line.startswith("─"):
                cleaned = line.lstrip("•-– ").strip()
                if cleaned:
                    sources.append(cleaned)
    return sources

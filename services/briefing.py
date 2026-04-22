from __future__ import annotations
import logging
import re
from typing import List, Tuple

from services.sheets import get_sheets
from services.notion import get_notion
from services.claude import get_claude
import utils
import config

logger = logging.getLogger(__name__)


async def create_and_send_briefing(context, chat_id: int) -> bool:
    """Generate a briefing, send it to chat_id, save to sheets + notion. Returns True on success."""
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

    # Extract source links from briefing text
    sources = extract_sources(briefing_text)

    # Send briefing in chunks if needed
    parts = utils.split_message(briefing_text)
    for part in parts:
        try:
            await context.bot.send_message(chat_id=chat_id, text=part)
        except Exception as e:
            logger.error("Failed to send briefing message: %s", e)
            return False

    # Save to sheets history
    notion_saved = False
    try:
        page_id = await notion.save_to_briefing_db(date_str, theme, briefing_text, sources)
        notion_saved = page_id is not None
    except Exception as e:
        logger.error("Notion save failed (non-fatal): %s", e)

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
        if "참고 출처" in line or "출처" in line:
            in_source_section = True
            continue
        if in_source_section:
            if line.startswith("※") or line.startswith("🗓") or line.startswith("📅"):
                break
            if line and not line.startswith("━"):
                sources.append(line.lstrip("- •*123456789.").strip())
    return [s for s in sources if s]

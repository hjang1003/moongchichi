from __future__ import annotations
import logging
import re
from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from services.sheets import get_sheets
from services.notion import get_notion
from services.claude import get_claude
import utils
import config

logger = logging.getLogger(__name__)

SECTION_SEP_RE = re.compile(r"^━{5,}$", re.MULTILINE)


def make_section_keyboard(date_str: str, section_idx: int, saved: bool = False) -> InlineKeyboardMarkup:
    save_btn = (
        InlineKeyboardButton("🗑️ 노션에서 삭제", callback_data=f"notion_delete:{date_str}:{section_idx}")
        if saved
        else InlineKeyboardButton("💾 노션에 저장", callback_data=f"notion_save:{date_str}:{section_idx}")
    )
    source_btn = InlineKeyboardButton("📎 소스 보기", callback_data=f"source_view:{date_str}")
    return InlineKeyboardMarkup([[save_btn, source_btn]])


def parse_briefing_sections(text: str) -> List[str]:
    """Split briefing on ━━━ separator lines; return non-empty chunks."""
    parts = SECTION_SEP_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


async def create_and_send_briefing(context, chat_id: int) -> bool:
    """Generate briefing, send per-section to chat_id, save to sheets + notion. Returns True on success."""
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

    try:
        profile = await sheets.get_profile()
    except Exception as e:
        logger.error("Failed to load profile from sheets: %s", e)
        profile = {}

    try:
        briefing_text = await claude.generate_briefing(profile, theme, date_str, weekday_ko)
    except Exception as e:
        logger.error("Failed to generate briefing: %s", e)
        return False

    sources = extract_sources(briefing_text)
    sections = parse_briefing_sections(briefing_text)

    # Structure: [header, item1, item2, item3, footer]
    # Content sections are everything between the first and last chunk.
    if len(sections) >= 3:
        header = sections[0]
        footer = sections[-1]
        content_sections = sections[1:-1]

        try:
            await context.bot.send_message(chat_id=chat_id, text=header)
        except Exception as e:
            logger.error("Failed to send briefing header: %s", e)
            return False

        for idx, section in enumerate(content_sections):
            keyboard = make_section_keyboard(date_str, idx)
            try:
                await context.bot.send_message(chat_id=chat_id, text=section, reply_markup=keyboard)
            except Exception as e:
                logger.error("Failed to send briefing section %d: %s", idx, e)
                return False

        cached_sections = content_sections
    else:
        # Fallback: send as split messages with one button on the last part
        parts = utils.split_message(briefing_text)
        for part in parts[:-1]:
            try:
                await context.bot.send_message(chat_id=chat_id, text=part)
            except Exception as e:
                logger.error("Failed to send briefing part: %s", e)
                return False
        keyboard = make_section_keyboard(date_str, 0)
        try:
            await context.bot.send_message(chat_id=chat_id, text=parts[-1], reply_markup=keyboard)
        except Exception as e:
            logger.error("Failed to send final briefing part: %s", e)
            return False
        cached_sections = parts

    context.bot_data[f"briefing:{date_str}"] = {
        "content": briefing_text,
        "theme": theme,
        "sources": sources,
        "sections": cached_sections,
        "saved_page_ids": {},
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

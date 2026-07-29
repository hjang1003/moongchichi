from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from services.sheets import get_sheets
from services.claude import get_claude
import utils
import config

logger = logging.getLogger(__name__)

SECTION_SEP_RE = re.compile(r"^━{5,}$", re.MULTILINE)


def make_section_keyboard(chat_id: int, date_str: str, section_idx: int, saved: bool = False) -> InlineKeyboardMarkup:
    save_btn = (
        InlineKeyboardButton("🗑️ 노션에서 삭제", callback_data=f"notion_delete:{chat_id}:{date_str}:{section_idx}")
        if saved
        else InlineKeyboardButton("💾 노션에 저장", callback_data=f"notion_save:{chat_id}:{date_str}:{section_idx}")
    )
    source_btn = InlineKeyboardButton("📎 소스 보기", callback_data=f"source_view:{chat_id}:{date_str}")
    return InlineKeyboardMarkup([[save_btn, source_btn]])


def parse_briefing_sections(text: str) -> List[str]:
    parts = SECTION_SEP_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


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


async def _generate_briefing_data(date_str: str, theme: str, weekday_ko: str) -> Optional[dict]:
    """Call Claude API once. Generate briefing content + extract keywords/sources + classify pools."""
    sheets = get_sheets()
    claude = get_claude()

    try:
        profile = await sheets.get_profile()
    except Exception as e:
        logger.error("Failed to load profile from sheets: %s", e)
        profile = {}

    try:
        recent_sources = await sheets.get_recent_sources(weeks=8)
        blocked = await sheets.get_blocked_keywords()
    except Exception as e:
        logger.error("Failed to load recent history from sheets: %s", e)
        recent_sources = []
        blocked = {"strict": [], "medium": []}

    try:
        briefing_text = await claude.generate_briefing(
            profile, theme, date_str, weekday_ko,
            recent_sources=recent_sources,
            blocked_keywords_strict=blocked.get("strict", []),
            blocked_keywords_medium=blocked.get("medium", []),
        )
    except Exception as e:
        logger.error("Failed to generate briefing: %s", e)
        return None

    sources = extract_sources(briefing_text)
    sections = parse_briefing_sections(briefing_text)

    try:
        keywords = await claude.extract_keywords(briefing_text)
    except Exception as e:
        logger.error("Keyword extraction failed: %s", e)
        keywords = []

    # 컨텐트 섹션만 추출해서 영역 분류
    content_sections = sections[1:-1] if len(sections) >= 3 else sections
    try:
        pools = await claude.classify_briefing_pools(content_sections)
    except Exception as e:
        logger.error("Pool classification failed: %s", e)
        pools = ["T"] * len(content_sections)

    return {
        "briefing_text": briefing_text,
        "sections": sections,
        "sources": sources,
        "keywords": keywords,
        "pools": pools,
        "theme": theme,
    }


async def _send_briefing_to_chat(context, chat_id: int, date_str: str, briefing_data: dict, include_buttons: bool = True) -> bool:
    """Send pre-generated briefing to one chat. If include_buttons=False, send plain text."""
    sections = briefing_data["sections"]
    briefing_text = briefing_data["briefing_text"]
    sources = briefing_data["sources"]
    theme = briefing_data.get("theme", "")

    if len(sections) >= 3:
        header = sections[0]
        content_sections = sections[1:-1]

        try:
            await context.bot.send_message(chat_id=chat_id, text=header)
        except Exception as e:
            logger.error("Failed to send briefing header to %s: %s", chat_id, e)
            return False

        for idx, section in enumerate(content_sections):
            reply_markup = make_section_keyboard(chat_id, date_str, idx) if include_buttons else None
            try:
                await context.bot.send_message(chat_id=chat_id, text=section, reply_markup=reply_markup)
            except Exception as e:
                logger.error("Failed to send briefing section %d to %s: %s", idx, chat_id, e)
                return False

        cached_sections = content_sections
    else:
        parts = utils.split_message(briefing_text)
        for part in parts[:-1]:
            try:
                await context.bot.send_message(chat_id=chat_id, text=part)
            except Exception as e:
                logger.error("Failed to send briefing part to %s: %s", chat_id, e)
                return False

        reply_markup = make_section_keyboard(chat_id, date_str, 0) if include_buttons else None
        try:
            await context.bot.send_message(chat_id=chat_id, text=parts[-1], reply_markup=reply_markup)
        except Exception as e:
            logger.error("Failed to send final briefing part to %s: %s", chat_id, e)
            return False

        cached_sections = parts

    if include_buttons:
        context.bot_data[f"briefing:{chat_id}:{date_str}"] = {
            "content": briefing_text,
            "theme": theme,
            "sources": sources,
            "sections": cached_sections,
            "saved_page_ids": {},
        }

    return True


async def create_and_send_briefing(context, chat_id: int) -> bool:
    """For /briefing command: generate + send to one chat + save history once."""
    sheets = get_sheets()
    now = utils.get_korea_now()
    date_str = utils.date_to_str(now)
    weekday_ko = config.WEEKDAY_KO.get(now.weekday(), "")
    theme = utils.get_weekday_theme(now)

    if not theme:
        logger.info("Today (%s) is not a weekday, skipping briefing.", date_str)
        return False

    briefing_data = await _generate_briefing_data(date_str, theme, weekday_ko)
    if not briefing_data:
        return False

    success = await _send_briefing_to_chat(context, chat_id, date_str, briefing_data, include_buttons=True)
    if not success:
        return False

    try:
        await sheets.add_history(
            date_str, theme, True, briefing_data["sources"], False,
            keywords=briefing_data["keywords"],
            pools=briefing_data["pools"],
        )
    except Exception as e:
        logger.error("Sheets history save failed: %s", e)

    return True


async def create_and_send_briefing_to_many(
    context,
    primary_chat_id: Optional[int],
    mirror_chat_ids: Optional[List[int]] = None,
) -> Tuple[List[int], List[int]]:
    """For scheduled daily briefing: generate ONCE, send to primary (with buttons) and mirrors (no buttons), save history ONCE.

    Returns (successes, failures): lists of chat_ids that succeeded / failed to receive the briefing.
    If today is not a briefing day (no theme), returns ([], []). If generation itself fails, all intended
    recipients are returned in failures.
    """
    if mirror_chat_ids is None:
        mirror_chat_ids = []

    intended: List[int] = []
    if primary_chat_id:
        intended.append(primary_chat_id)
    for cid in mirror_chat_ids:
        if cid and cid != primary_chat_id and cid not in intended:
            intended.append(cid)

    sheets = get_sheets()
    now = utils.get_korea_now()
    date_str = utils.date_to_str(now)
    weekday_ko = config.WEEKDAY_KO.get(now.weekday(), "")
    theme = utils.get_weekday_theme(now)

    if not theme:
        logger.info("Today (%s) is not a weekday, skipping briefing.", date_str)
        return [], []

    briefing_data = await _generate_briefing_data(date_str, theme, weekday_ko)
    if not briefing_data:
        return [], list(intended)

    successes: List[int] = []
    failures: List[int] = []

    if primary_chat_id:
        try:
            ok = await _send_briefing_to_chat(
                context, primary_chat_id, date_str, briefing_data, include_buttons=True
            )
        except Exception as e:
            logger.error("Failed to send primary briefing to %s: %s", primary_chat_id, e)
            ok = False
        (successes if ok else failures).append(primary_chat_id)

    for chat_id in mirror_chat_ids:
        if not chat_id or chat_id == primary_chat_id:
            continue
        try:
            ok = await _send_briefing_to_chat(
                context, chat_id, date_str, briefing_data, include_buttons=False
            )
        except Exception as e:
            logger.error("Failed to send mirror briefing to %s: %s", chat_id, e)
            ok = False
        (successes if ok else failures).append(chat_id)

    if successes:
        try:
            await sheets.add_history(
                date_str, theme, True, briefing_data["sources"], False,
                keywords=briefing_data["keywords"],
                pools=briefing_data["pools"],
            )
        except Exception as e:
            logger.error("Sheets history save failed: %s", e)

    return successes, failures

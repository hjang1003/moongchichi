from __future__ import annotations
import logging

from telegram import Update
from telegram.ext import ContextTypes

from services.sheets import get_sheets
from services.notion import get_notion
from services.briefing import make_section_keyboard

logger = logging.getLogger(__name__)


def _parse_cb(data: str) -> tuple[str, int]:
    """Return (date_str, section_idx) from 'action:date_str' or 'action:date_str:idx'."""
    parts = data.split(":", 2)
    date_str = parts[1] if len(parts) > 1 else ""
    section_idx = int(parts[2]) if len(parts) > 2 else 0
    return date_str, section_idx


async def handle_notion_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    date_str, section_idx = _parse_cb(query.data)

    briefing_data = context.bot_data.get(f"briefing:{date_str}")
    if not briefing_data:
        briefing_data = await _load_briefing_data_from_sheets(date_str)

    if not briefing_data:
        await query.answer("브리핑 데이터를 찾지 못했어요 😥", show_alert=True)
        return

    sections = briefing_data.get("sections", [])
    if sections and section_idx < len(sections):
        content = sections[section_idx]
    else:
        content = briefing_data.get("content", "")
    theme = briefing_data.get("theme", "")

    notion = get_notion()
    page_id = await notion.save_to_saved_db(date_str, theme, content)

    if not page_id:
        await query.answer("저장에 실패했어요 😥 잠시 후 다시 시도해 주세요.", show_alert=True)
        return

    saved_page_ids = briefing_data.setdefault("saved_page_ids", {})
    saved_page_ids[str(section_idx)] = page_id
    context.bot_data[f"briefing:{date_str}"] = briefing_data

    try:
        sheets = get_sheets()
        await sheets.update_notion_saved(date_str)
    except Exception as e:
        logger.error("Sheets update_notion_saved failed: %s", e)

    await query.edit_message_reply_markup(reply_markup=make_section_keyboard(date_str, section_idx, saved=True))
    await query.answer("✅ 노션에 저장됐어요!", show_alert=True)


async def handle_notion_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    date_str, section_idx = _parse_cb(query.data)

    briefing_data = context.bot_data.get(f"briefing:{date_str}", {})
    saved_page_ids = briefing_data.get("saved_page_ids", {})
    page_id = saved_page_ids.get(str(section_idx))

    if not page_id:
        await query.answer("삭제할 항목을 찾지 못했어요.", show_alert=True)
        return

    notion = get_notion()
    success = await notion.delete_page(page_id)

    if not success:
        await query.answer("삭제에 실패했어요 😥 잠시 후 다시 시도해 주세요.", show_alert=True)
        return

    saved_page_ids.pop(str(section_idx), None)
    context.bot_data[f"briefing:{date_str}"] = briefing_data

    await query.edit_message_reply_markup(reply_markup=make_section_keyboard(date_str, section_idx, saved=False))
    await query.answer("🗑️ 노션에서 삭제됐어요!", show_alert=True)


async def handle_source_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    date_str = query.data.split(":", 2)[1]

    sources: list[str] = []

    briefing_data = context.bot_data.get(f"briefing:{date_str}")
    if briefing_data:
        sources = briefing_data.get("sources", [])

    if not sources:
        try:
            sheets = get_sheets()
            history = await sheets.get_history_by_date(date_str)
            if history:
                raw = history.get("소스링크", "")
                sources = [s.strip() for s in raw.split("|") if s.strip()]
        except Exception as e:
            logger.error("Source fallback from sheets failed: %s", e)

    await query.answer()

    if sources:
        formatted = "\n".join([f"• {s}" for s in sources])
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"📎 소스 링크 ({date_str})\n\n{formatted}",
        )
    else:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="저장된 소스 링크가 없어요.",
        )


async def _load_briefing_data_from_sheets(date_str: str) -> dict | None:
    try:
        sheets = get_sheets()
        history = await sheets.get_history_by_date(date_str)
        if not history:
            return None
        raw_sources = history.get("소스링크", "")
        sources = [s.strip() for s in raw_sources.split("|") if s.strip()]
        return {
            "content": "",
            "theme": history.get("요일테마", ""),
            "sources": sources,
            "saved_page_ids": {},
        }
    except Exception:
        return None

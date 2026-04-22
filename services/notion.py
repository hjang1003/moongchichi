from __future__ import annotations
import datetime
import logging
import re
from typing import List, Optional

from notion_client import AsyncClient

import config

logger = logging.getLogger(__name__)

RICH_TEXT_LIMIT = 2000


def _normalize_notion_id(raw_id: str) -> str:
    """Ensure Notion ID is in 8-4-4-4-12 UUID format.

    Handles full URLs like https://www.notion.so/DB-34a1205853e280978a58e6213c507e42
    by extracting the 32-char hex ID from the end of the last path segment.
    """
    clean = raw_id.strip()
    # Already in UUID format
    if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", clean, re.I):
        return clean
    # Strip URL down to last path segment (handles https://www.notion.so/DB-<hex> forms)
    if "/" in clean:
        clean = clean.rstrip("/").split("/")[-1]
    # Remove query string
    if "?" in clean:
        clean = clean.split("?")[0]
    # Find exactly 32 consecutive hex chars at the end of the segment (handles "DB-<32hex>" prefix)
    match = re.search(r"[0-9a-f]{32}$", clean, re.I)
    if match:
        h = match.group(0).lower()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
    # Fallback: strip all non-hex chars; if exactly 32 remain, use them
    hex_only = re.sub(r"[^0-9a-fA-F]", "", clean)
    if len(hex_only) == 32:
        return f"{hex_only[:8]}-{hex_only[8:12]}-{hex_only[12:16]}-{hex_only[16:20]}-{hex_only[20:]}"
    return clean


def _content_to_blocks(content: str) -> List[dict]:
    """Split content into paragraph blocks, each rich_text chunk ≤ 2000 chars."""
    blocks = []
    for i in range(0, max(len(content), 1), RICH_TEXT_LIMIT):
        chunk = content[i : i + RICH_TEXT_LIMIT]
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": chunk}}]
            },
        })
    return blocks


BLOCK_BATCH_SIZE = 100  # Notion API limit per append call


def _extract_section_title(content: str, date_str: str) -> str:
    """Extract first non-empty line as title, prefix with [요일], truncate to 15 chars."""
    try:
        d = datetime.date.fromisoformat(date_str)
        weekday_ko = config.WEEKDAY_KO.get(d.weekday(), "")
        prefix = f"[{weekday_ko}] " if weekday_ko else ""
    except Exception:
        prefix = ""
    title = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if len(title) > 15:
        title = title[:15] + "..."
    return f"{prefix}{title}"


class NotionService:
    def __init__(self):
        self._client = AsyncClient(auth=config.NOTION_API_KEY)
        self._briefing_db_id = _normalize_notion_id(config.NOTION_BRIEFING_DATABASE_ID)
        self._saved_db_id = _normalize_notion_id(config.NOTION_SAVED_DATABASE_ID)

    async def _append_blocks(self, page_id: str, blocks: List[dict]) -> None:
        """Append blocks to a page in batches of 100 (Notion API limit)."""
        for i in range(0, len(blocks), BLOCK_BATCH_SIZE):
            await self._client.blocks.children.append(
                block_id=page_id,
                children=blocks[i : i + BLOCK_BATCH_SIZE],
            )

    async def save_to_briefing_db(
        self,
        date_str: str,
        theme: str,
        content: str,
        sources: List[str],
        saved: bool = False,
    ) -> Optional[str]:
        try:
            sources_text = "\n".join(sources)[:RICH_TEXT_LIMIT]
            response = await self._client.pages.create(
                parent={"database_id": self._briefing_db_id},
                properties={
                    "날짜": {"date": {"start": date_str}},
                    "요일테마": {"title": [{"text": {"content": theme[:255]}}]},
                    "저장여부": {"checkbox": saved},
                    "소스링크": {"rich_text": [{"type": "text", "text": {"content": sources_text}}]},
                },
            )
            page_id = response["id"]
            await self._append_blocks(page_id, _content_to_blocks(content))
            return page_id
        except Exception as e:
            logger.error("Notion save_to_briefing_db failed: %s", e)
            return None

    async def save_to_saved_db(
        self,
        date_str: str,
        theme: str,
        content: str,
    ) -> Optional[str]:
        try:
            section_title = _extract_section_title(content, date_str)
            response = await self._client.pages.create(
                parent={"database_id": self._saved_db_id},
                properties={
                    "날짜": {"date": {"start": date_str}},
                    "요일테마": {"title": [{"text": {"content": theme[:255]}}]},
                    "제목": {"rich_text": [{"type": "text", "text": {"content": section_title}}]},
                },
            )
            page_id = response["id"]
            await self._append_blocks(page_id, _content_to_blocks(content))
            return page_id
        except Exception as e:
            logger.error("Notion save_to_saved_db failed: %s", e)
            return None

    async def update_saved_flag(self, page_id: str) -> None:
        try:
            await self._client.pages.update(
                page_id=page_id,
                properties={"저장여부": {"checkbox": True}},
            )
        except Exception as e:
            logger.error("Notion update_saved_flag failed: %s", e)

    async def get_saved_briefings_this_month(self, year: int, month: int) -> List[dict]:
        try:
            start = f"{year}-{month:02d}-01"
            end = f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"
            response = await self._client.databases.query(
                database_id=self._saved_db_id,
                filter={
                    "and": [
                        {"property": "날짜", "date": {"on_or_after": start}},
                        {"property": "날짜", "date": {"before": end}},
                    ]
                },
            )
            results = []
            for page in response.get("results", []):
                props = page["properties"]
                title_items = props.get("요일테마", {}).get("title", [])
                theme = title_items[0]["text"]["content"] if title_items else ""
                date_val = props.get("날짜", {}).get("date") or {}
                date_str = date_val.get("start", "")
                content = await self._get_page_content(page["id"])
                results.append({"date": date_str, "theme": theme, "content": content})
            return results
        except Exception as e:
            logger.error("Notion get_saved_briefings_this_month failed: %s", e)
            return []

    async def _get_page_content(self, page_id: str) -> str:
        try:
            blocks = await self._client.blocks.children.list(block_id=page_id)
            texts = []
            for block in blocks.get("results", []):
                if block["type"] == "paragraph":
                    for rt in block["paragraph"].get("rich_text", []):
                        texts.append(rt.get("text", {}).get("content", ""))
            return "\n".join(texts)
        except Exception:
            return ""

    async def delete_page(self, page_id: str) -> bool:
        try:
            await self._client.pages.update(page_id=page_id, archived=True)
            return True
        except Exception as e:
            logger.error("Notion delete_page failed: %s", e)
            return False

    async def ensure_databases(self, sheets) -> None:
        """Auto-create the saved-briefings DB if the ID is not set in env vars.

        Priority: env var → Sheets setting → create via API (requires NOTION_PARENT_PAGE_ID).
        Created ID is saved to Sheets so it survives restarts without the env var.
        """
        parent_page_id = (
            _normalize_notion_id(config.NOTION_PARENT_PAGE_ID)
            if config.NOTION_PARENT_PAGE_ID
            else ""
        )

        # Saved briefings DB
        if not self._saved_db_id:
            saved = await sheets.get_setting("NOTION_SAVED_DATABASE_ID")
            if saved:
                self._saved_db_id = _normalize_notion_id(saved)

        if not self._saved_db_id:
            if not parent_page_id:
                logger.warning("NOTION_SAVED_DATABASE_ID not set and NOTION_PARENT_PAGE_ID missing — skipping auto-create.")
            else:
                db_id = await self._create_saved_db(parent_page_id)
                if db_id:
                    self._saved_db_id = db_id
                    await sheets.set_setting("NOTION_SAVED_DATABASE_ID", db_id)
                    logger.info("Auto-created Notion saved briefings DB: %s", db_id)

    async def _create_briefing_db(self, parent_page_id: str) -> str:
        try:
            response = await self._client.databases.create(
                parent={"type": "page_id", "page_id": parent_page_id},
                title=[{"type": "text", "text": {"content": "마케팅 브리핑"}}],
                properties={
                    "요일테마": {"title": {}},
                    "날짜": {"date": {}},
                    "저장여부": {"checkbox": {}},
                    "소스링크": {"rich_text": {}},
                },
            )
            return _normalize_notion_id(response["id"])
        except Exception as e:
            logger.error("Failed to create briefing DB in Notion: %s", e)
            return ""

    async def _create_saved_db(self, parent_page_id: str) -> str:
        try:
            response = await self._client.databases.create(
                parent={"type": "page_id", "page_id": parent_page_id},
                title=[{"type": "text", "text": {"content": "저장된 브리핑"}}],
                properties={
                    "요일테마": {"title": {}},
                    "제목": {"rich_text": {}},
                    "날짜": {"date": {}},
                    "메모": {"rich_text": {}},
                },
            )
            return _normalize_notion_id(response["id"])
        except Exception as e:
            logger.error("Failed to create saved briefings DB in Notion: %s", e)
            return ""

    async def reset_briefing_db(self) -> None:
        try:
            response = await self._client.databases.query(database_id=self._briefing_db_id)
            for page in response.get("results", []):
                await self._client.pages.update(page_id=page["id"], archived=True)
        except Exception as e:
            logger.error("Notion reset_briefing_db failed: %s", e)

    async def reset_saved_db(self) -> None:
        try:
            response = await self._client.databases.query(database_id=self._saved_db_id)
            for page in response.get("results", []):
                await self._client.pages.update(page_id=page["id"], archived=True)
        except Exception as e:
            logger.error("Notion reset_saved_db failed: %s", e)


_notion_service: NotionService | None = None


def get_notion() -> NotionService:
    global _notion_service
    if _notion_service is None:
        _notion_service = NotionService()
    return _notion_service

from __future__ import annotations
import logging
import re
from typing import List, Optional

from notion_client import AsyncClient

import config

logger = logging.getLogger(__name__)

RICH_TEXT_LIMIT = 2000


def _normalize_notion_id(raw_id: str) -> str:
    """Ensure Notion ID is in 8-4-4-4-12 UUID format."""
    clean = raw_id.strip()
    # Already in UUID format
    if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", clean, re.I):
        return clean
    # 32 hex chars without hyphens → insert hyphens
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


class NotionService:
    def __init__(self):
        self._client = AsyncClient(auth=config.NOTION_API_KEY)
        self._briefing_db_id = _normalize_notion_id(config.NOTION_BRIEFING_DATABASE_ID)
        self._saved_db_id = _normalize_notion_id(config.NOTION_SAVED_DATABASE_ID)

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
                children=_content_to_blocks(content),
            )
            return response["id"]
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
            response = await self._client.pages.create(
                parent={"database_id": self._saved_db_id},
                properties={
                    "날짜": {"date": {"start": date_str}},
                    "요일테마": {"title": [{"text": {"content": theme[:255]}}]},
                },
                children=_content_to_blocks(content),
            )
            return response["id"]
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

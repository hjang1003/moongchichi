from __future__ import annotations
import logging
from datetime import date, datetime
from typing import List, Optional

from notion_client import AsyncClient

import config

logger = logging.getLogger(__name__)


class NotionService:
    def __init__(self):
        self._client = AsyncClient(auth=config.NOTION_API_KEY)

    async def save_to_briefing_db(
        self,
        date_str: str,
        theme: str,
        content: str,
        sources: List[str],
        saved: bool = False,
    ) -> Optional[str]:
        try:
            response = await self._client.pages.create(
                parent={"database_id": config.NOTION_BRIEFING_DATABASE_ID},
                properties={
                    "날짜": {"date": {"start": date_str}},
                    "요일테마": {"title": [{"text": {"content": theme}}]},
                    "저장여부": {"checkbox": saved},
                    "소스링크": {"rich_text": [{"text": {"content": "\n".join(sources)}}]},
                },
                children=[
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": content[:2000]}}]
                        },
                    },
                    *(
                        [
                            {
                                "object": "block",
                                "type": "paragraph",
                                "paragraph": {
                                    "rich_text": [{"type": "text", "text": {"content": content[2000:4000]}}]
                                },
                            }
                        ]
                        if len(content) > 2000
                        else []
                    ),
                ],
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
                parent={"database_id": config.NOTION_SAVED_DATABASE_ID},
                properties={
                    "날짜": {"date": {"start": date_str}},
                    "요일테마": {"title": [{"text": {"content": theme}}]},
                },
                children=[
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": content[:2000]}}]
                        },
                    },
                    *(
                        [
                            {
                                "object": "block",
                                "type": "paragraph",
                                "paragraph": {
                                    "rich_text": [{"type": "text", "text": {"content": content[2000:4000]}}]
                                },
                            }
                        ]
                        if len(content) > 2000
                        else []
                    ),
                ],
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
            if month == 12:
                end = f"{year + 1}-01-01"
            else:
                end = f"{year}-{month + 1:02d}-01"

            response = await self._client.databases.query(
                database_id=config.NOTION_SAVED_DATABASE_ID,
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
                theme = ""
                title_items = props.get("요일테마", {}).get("title", [])
                if title_items:
                    theme = title_items[0]["text"]["content"]
                date_val = props.get("날짜", {}).get("date", {})
                date_str = date_val.get("start", "") if date_val else ""
                # Get content from blocks
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
            response = await self._client.databases.query(
                database_id=config.NOTION_BRIEFING_DATABASE_ID
            )
            for page in response.get("results", []):
                await self._client.pages.update(page_id=page["id"], archived=True)
        except Exception as e:
            logger.error("Notion reset_briefing_db failed: %s", e)

    async def reset_saved_db(self) -> None:
        try:
            response = await self._client.databases.query(
                database_id=config.NOTION_SAVED_DATABASE_ID
            )
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

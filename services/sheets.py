from __future__ import annotations
import asyncio
import json
import logging
from datetime import date
from functools import partial
from typing import Any, Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

import config
from utils import date_to_str

logger = logging.getLogger(__name__)

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

SETTINGS_TAB = "설정"
PROFILE_TAB = "프로필"
HISTORY_TAB = "브리핑이력"

HISTORY_HEADERS = ["날짜", "요일테마", "발송여부", "소스링크", "노션저장여부"]


class SheetsService:
    def __init__(self):
        creds_dict = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        self._client = gspread.authorize(creds)
        self._spreadsheet = None

    def _get_spreadsheet(self):
        if self._spreadsheet is None:
            self._spreadsheet = self._client.open_by_key(config.GOOGLE_SHEETS_ID)
        return self._spreadsheet

    def _get_tab(self, tab_name: str):
        return self._get_spreadsheet().worksheet(tab_name)

    # ── Settings ──────────────────────────────────────────────────────────

    def _sync_get_setting(self, key: str) -> str:
        ws = self._get_tab(SETTINGS_TAB)
        records = ws.get_all_values()
        for row in records:
            if row and row[0] == key:
                return row[1] if len(row) > 1 else ""
        return ""

    def _sync_set_setting(self, key: str, value: str) -> None:
        ws = self._get_tab(SETTINGS_TAB)
        records = ws.get_all_values()
        for i, row in enumerate(records, start=1):
            if row and row[0] == key:
                ws.update_cell(i, 2, value)
                return
        ws.append_row([key, value])

    async def get_setting(self, key: str) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_get_setting, key)

    async def set_setting(self, key: str, value: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_set_setting, key, value)

    # ── Profile ───────────────────────────────────────────────────────────

    def _sync_get_profile(self) -> Dict[str, str]:
        ws = self._get_tab(PROFILE_TAB)
        records = ws.get_all_values()
        return {row[0]: (row[1] if len(row) > 1 else "") for row in records if row}

    def _sync_set_profile(self, field: str, value: str) -> None:
        ws = self._get_tab(PROFILE_TAB)
        records = ws.get_all_values()
        for i, row in enumerate(records, start=1):
            if row and row[0] == field:
                ws.update_cell(i, 2, value)
                return
        ws.append_row([field, value])

    def _sync_set_profile_bulk(self, data: Dict[str, str]) -> None:
        for field, value in data.items():
            self._sync_set_profile(field, value)

    async def get_profile(self) -> Dict[str, str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_get_profile)

    async def set_profile(self, field: str, value: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_set_profile, field, value)

    async def set_profile_bulk(self, data: Dict[str, str]) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_set_profile_bulk, data)

    # ── Briefing History ──────────────────────────────────────────────────

    def _sync_add_history(
        self,
        date_str: str,
        theme: str,
        sent: bool,
        sources: List[str],
        notion_saved: bool,
    ) -> None:
        ws = self._get_tab(HISTORY_TAB)
        # Ensure header row exists
        existing = ws.get_all_values()
        if not existing or existing[0] != HISTORY_HEADERS:
            ws.insert_row(HISTORY_HEADERS, 1)
        ws.append_row([
            date_str,
            theme,
            str(sent),
            "|".join(sources),
            str(notion_saved),
        ])

    def _sync_get_history(self, limit: int = 10) -> List[Dict]:
        ws = self._get_tab(HISTORY_TAB)
        records = ws.get_all_records()
        return records[-limit:] if len(records) > limit else records

    def _sync_get_history_by_date(self, date_str: str) -> Optional[Dict]:
        ws = self._get_tab(HISTORY_TAB)
        records = ws.get_all_records()
        for row in records:
            if row.get("날짜") == date_str:
                return row
        return None

    def _sync_update_notion_saved(self, date_str: str) -> None:
        ws = self._get_tab(HISTORY_TAB)
        records = ws.get_all_values()
        headers = records[0] if records else []
        if "날짜" not in headers:
            return
        date_col = headers.index("날짜") + 1
        notion_col = headers.index("노션저장여부") + 1 if "노션저장여부" in headers else None
        if not notion_col:
            return
        for i, row in enumerate(records[1:], start=2):
            if row and row[date_col - 1] == date_str:
                ws.update_cell(i, notion_col, "True")
                return

    def _sync_get_saved_history_this_month(self, year: int, month: int) -> List[Dict]:
        ws = self._get_tab(HISTORY_TAB)
        records = ws.get_all_records()
        prefix = f"{year}-{month:02d}"
        return [
            r for r in records
            if str(r.get("날짜", "")).startswith(prefix)
            and str(r.get("노션저장여부", "")).lower() == "true"
        ]

    def _sync_reset_history(self) -> None:
        ws = self._get_tab(HISTORY_TAB)
        ws.clear()
        ws.append_row(HISTORY_HEADERS)

    async def add_history(self, date_str: str, theme: str, sent: bool, sources: List[str], notion_saved: bool) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_add_history, date_str, theme, sent, sources, notion_saved)

    async def get_history(self, limit: int = 10) -> List[Dict]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_get_history, limit)

    async def get_history_by_date(self, date_str: str) -> Optional[Dict]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_get_history_by_date, date_str)

    async def update_notion_saved(self, date_str: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_update_notion_saved, date_str)

    async def get_saved_history_this_month(self, year: int, month: int) -> List[Dict]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_get_saved_history_this_month, year, month)

    async def reset_history(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_reset_history)

    # ── Auth helpers ──────────────────────────────────────────────────────

    async def is_authenticated(self, chat_id: int) -> bool:
        user_id = await self.get_setting("사용자_ChatID")
        admin_id = await self.get_setting("관리자_ChatID")
        valid_ids = {s.strip() for s in [str(user_id), str(admin_id)] if s and s.strip()}
        return str(chat_id) in valid_ids

    async def is_admin(self, chat_id: int) -> bool:
        admin_id = await self.get_setting("관리자_ChatID")
        return bool(admin_id) and str(chat_id) == str(admin_id).strip()

    async def is_active(self) -> bool:
        status = await self.get_setting("봇_상태")
        return status != "비활성"

    async def reset_all(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_reset_all)

    def _sync_reset_all(self) -> None:
        # Clear profile tab
        ws_profile = self._get_tab(PROFILE_TAB)
        profile_keys = [
            "목표직무", "경력수준", "관심업종", "관심플랫폼", "글로벌여부",
            "기타요청", "관심업종비율", "인접산업비율", "전체트렌드비율",
            "온보딩완료", "온보딩날짜",
        ]
        ws_profile.clear()
        for key in profile_keys:
            default = "60" if key == "관심업종비율" else ("30" if key == "인접산업비율" else ("10" if key == "전체트렌드비율" else "false" if key == "온보딩완료" else ""))
            ws_profile.append_row([key, default])
        # Clear history
        self._sync_reset_history()
        # Clear user ChatID
        self._sync_set_setting("사용자_ChatID", "")


_sheets_service: Optional[SheetsService] = None


def get_sheets() -> SheetsService:
    global _sheets_service
    if _sheets_service is None:
        _sheets_service = SheetsService()
    return _sheets_service

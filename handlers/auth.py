from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple

import pytz
from telegram import Update
from telegram.ext import ContextTypes

import config
from services.sheets import get_sheets

logger = logging.getLogger(__name__)

# In-memory: {chat_id: {"attempts": int, "blocked_until": datetime | None}}
_auth_state: Dict[int, Dict] = {}


def _get_state(chat_id: int) -> Dict:
    if chat_id not in _auth_state:
        _auth_state[chat_id] = {"attempts": 0, "blocked_until": None}
    return _auth_state[chat_id]


def is_blocked(chat_id: int) -> bool:
    state = _get_state(chat_id)
    if state["blocked_until"] is None:
        return False
    tz = pytz.timezone(config.KOREA_TZ)
    now = datetime.now(tz)
    if now < state["blocked_until"]:
        return True
    # Block expired
    state["blocked_until"] = None
    state["attempts"] = 0
    return False


def block_until(chat_id: int) -> datetime:
    tz = pytz.timezone(config.KOREA_TZ)
    until = datetime.now(tz) + timedelta(hours=config.LOCKOUT_HOURS)
    _auth_state[chat_id] = {"attempts": config.MAX_PASSWORD_ATTEMPTS, "blocked_until": until}
    return until


def increment_attempts(chat_id: int) -> int:
    state = _get_state(chat_id)
    state["attempts"] += 1
    return state["attempts"]


def reset_attempts(chat_id: int) -> None:
    _auth_state[chat_id] = {"attempts": 0, "blocked_until": None}


def remaining_attempts(chat_id: int) -> int:
    state = _get_state(chat_id)
    return max(0, config.MAX_PASSWORD_ATTEMPTS - state["attempts"])


async def check_password(password_input: str) -> bool:
    sheets = get_sheets()
    correct = await sheets.get_setting("비밀번호")
    return password_input.strip() == correct.strip()


async def notify_admin_lockout(context, chat_id: int) -> None:
    try:
        sheets = get_sheets()
        admin_id = await sheets.get_setting("관리자_ChatID")
        if admin_id:
            await context.bot.send_message(
                chat_id=int(admin_id),
                text=f"⚠️ 비밀번호 5회 오류: Chat ID {chat_id} 가 24시간 차단되었습니다.",
            )
    except Exception as e:
        logger.error("Failed to notify admin of lockout: %s", e)


async def require_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if user is authenticated. Sends error message and returns False if not."""
    chat_id = update.effective_chat.id
    sheets = get_sheets()
    if await sheets.is_authenticated(chat_id):
        return True
    await update.message.reply_text(
        "🔒 인증이 필요해요! /start 를 눌러서 시작해 주세요."
    )
    return False

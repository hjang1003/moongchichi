from __future__ import annotations
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from handlers.auth import require_auth
from handlers.admin import handle_reset_confirm
from services.sheets import get_sheets
from services.claude import get_claude
from services.notion import get_notion
from scheduler import reschedule_jobs
import utils
import config

logger = logging.getLogger(__name__)



async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    sheets = get_sheets()
    await sheets.set_setting("봇_상태", "비활성")
    await update.message.reply_text("⏸️ 브리핑이 일시 중단되었어요.\n다시 받으려면 /resume 을 눌러 주세요!")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    sheets = get_sheets()
    await sheets.set_setting("봇_상태", "활성")
    await update.message.reply_text("▶️ 브리핑이 재개되었어요! 내일 아침 8시에 보내드릴게요 🐶")


async def cmd_alarm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    args = context.args
    if not args:
        sheets = get_sheets()
        current = await sheets.get_setting("알람_시간")
        await update.message.reply_text(
            f"현재 알람 시간: {current or '08:00'}\n\n"
            f"변경하려면: /alarm 09:00"
        )
        return

    time_str = args[0].strip()
    if not re.match(r"^\d{2}:\d{2}$", time_str):
        await update.message.reply_text(
            "시간 형식이 맞지 않아요!\n예: /alarm 09:00"
        )
        return

    hour, minute = map(int, time_str.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        await update.message.reply_text("올바른 시간을 입력해 주세요!")
        return

    sheets = get_sheets()
    await sheets.set_setting("알람_시간", time_str)
    reschedule_jobs(context.job_queue, time_str)
    await update.message.reply_text(
        f"⏰ 알람 시간이 {time_str} (KST)으로 변경되었어요!"
    )


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    args = context.args
    sheets = get_sheets()

    if not args:
        profile = await sheets.get_profile()
        alarm_time = await sheets.get_setting("알람_시간")
        bot_status = await sheets.get_setting("봇_상태")
        additional = profile.get("추가요청사항", "")
        additional_display = (additional[:200] + "...") if len(additional) > 200 else (additional or "-")
        last_updated = profile.get("요청사항_업데이트일", "")
        additional_line = f"\n추가요청사항: {additional_display}" + (f" ({last_updated})" if last_updated else "")
        await update.message.reply_text(
            f"👤 프로필\n"
            f"목표직무: {profile.get('목표직무', '-')}\n"
            f"경력수준: {profile.get('경력수준', '-')}\n"
            f"관심업종: {profile.get('관심업종', '-')}\n"
            f"관심플랫폼: {profile.get('관심플랫폼', '-')}\n"
            f"글로벌여부: {profile.get('글로벌여부', '-')}\n"
            f"기타요청: {profile.get('기타요청', '-')}"
            f"{additional_line}\n\n"
            f"📊 콘텐츠 비율\n"
            f"관심업종비율: {profile.get('관심업종비율', '60')}%\n"
            f"인접산업비율: {profile.get('인접산업비율', '30')}%\n"
            f"전체트렌드비율: {profile.get('전체트렌드비율', '10')}%\n\n"
            f"⚙️ 브리핑 설정\n"
            f"알람 시간: {alarm_time or '08:00'} (KST)\n"
            f"봇 상태: {bot_status or '활성'}\n\n"
            f"✏️ 프로필 수정: /update [항목] [값]\n"
            f"예: /update 목표직무 퍼포먼스마케터\n"
            f"수정 가능 항목: 목표직무, 경력수준, 관심업종, 관심플랫폼, 글로벌여부, 기타요청\n\n"
            f"비율 변경: /profile ratio 50 40 10\n"
            f"(관심업종 % / 인접산업 % / 전체트렌드 %)"
        )
        return

    if args[0] == "ratio" and len(args) == 4:
        try:
            a, b, c = int(args[1]), int(args[2]), int(args[3])
            if a + b + c != 100:
                await update.message.reply_text("세 숫자의 합이 100이 되어야 해요!")
                return
            await sheets.set_profile("관심업종비율", str(a))
            await sheets.set_profile("인접산업비율", str(b))
            await sheets.set_profile("전체트렌드비율", str(c))
            await update.message.reply_text(
                f"✅ 비율이 업데이트되었어요!\n"
                f"관심업종 {a}% / 인접산업 {b}% / 전체트렌드 {c}%"
            )
        except ValueError:
            await update.message.reply_text("숫자를 입력해 주세요!\n예: /profile ratio 50 40 10")
    else:
        await update.message.reply_text(
            "올바른 형식: /profile ratio 50 40 10\n"
            "(관심업종 % / 인접산업 % / 전체트렌드 %)"
        )


UPDATABLE_FIELDS = ["목표직무", "경력수준", "관심업종", "관심플랫폼", "글로벌여부", "기타요청"]


async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "수정할 항목과 값을 입력해 주세요!\n"
            "예: /update 목표직무 퍼포먼스마케터\n\n"
            f"수정 가능 항목: {', '.join(UPDATABLE_FIELDS)}"
        )
        return

    field = args[0]
    value = " ".join(args[1:])

    if field not in UPDATABLE_FIELDS:
        await update.message.reply_text(
            f"'{field}'은 수정할 수 없는 항목이에요.\n"
            f"수정 가능 항목: {', '.join(UPDATABLE_FIELDS)}"
        )
        return

    sheets = get_sheets()
    profile = await sheets.get_profile()
    old_value = profile.get(field, "-")
    try:
        await sheets.set_profile(field, value)
        await update.message.reply_text(
            f"✅ {field}이(가) [{old_value}] → [{value}]으로 변경됐어요!"
        )
    except Exception as e:
        logger.error("cmd_update set_profile failed: %s", e)
        await update.message.reply_text("일시적인 오류가 발생했어요 😥 잠시 후 다시 시도해 주세요!")


async def cmd_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "저장할 요청사항을 입력해주세요.\n예: /request 브리핑 수준 올려줘"
        )
        return

    request_text = " ".join(args)
    now = utils.get_korea_now()
    date_str = utils.date_to_str(now)
    sheets = get_sheets()
    try:
        await sheets.append_additional_request(request_text, date_str)
        await update.message.reply_text("요청사항이 저장됐어요! 다음 브리핑부터 반영할게요 😊")
    except Exception as e:
        logger.error("append_additional_request failed: %s", e)
        await update.message.reply_text("일시적인 오류가 발생했어요 😥 잠시 후 다시 시도해 주세요!")



async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "피드백 내용을 입력해 주세요!\n예: /feedback 브리핑이 너무 길어요"
        )
        return

    feedback_text = " ".join(args)
    chat_id = update.effective_chat.id
    sheets = get_sheets()

    try:
        admin_id = await sheets.get_setting("관리자_ChatID")
        if admin_id:
            await context.bot.send_message(
                chat_id=int(admin_id),
                text=f"💬 피드백 (from {chat_id}):\n\n{feedback_text}",
            )
        await update.message.reply_text("✅ 피드백을 전달했어요! 감사해요 😊")
    except Exception as e:
        logger.error("Feedback send failed: %s", e)
        await update.message.reply_text("일시적인 오류가 발생했어요 😥 잠시 후 다시 시도해 주세요!")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🐾 뭉치치 명령어 목록\n\n"
        "/alarm [시간] — 알람 시간 변경 (예: /alarm 09:00)\n"
        "/briefing — 지금 즉시 브리핑\n"
        "/feedback [내용] — 개발자에게 전달\n"
        "/pause — 브리핑 일시 중단\n"
        "/profile — 프로필 및 설정 전체 보기\n"
        "/recap [날짜] — 특정 날짜 브리핑 이력 보기\n"
        "/request [내용] — 브리핑 관련 요청사항 저장\n"
        "/resume — 브리핑 재개\n"
        "/schedule — 요일별 브리핑 테마 안내\n"
        "/term [용어] — 마케팅 용어 설명\n"
        "/topic [주제] — 특정 주제 브리핑\n"
        "/update [항목] [값] — 프로필 항목 수정\n"
        "/help — 명령어 목록\n\n"
        "💬 자연어로 질문도 가능해요!\n"
        "예: '저번 주 캠페인 사례 뭐였더라?'"
    )


async def handle_natural_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    sheets = get_sheets()

    if not await sheets.is_authenticated(chat_id):
        await update.message.reply_text(
            "안녕하세요! /start 를 눌러서 시작해 주세요 🐾"
        )
        return

    # Check for pending reset confirmation
    if context.user_data.get("pending_reset") and await sheets.is_admin(chat_id):
        await handle_reset_confirm(update, context)
        return

    # Check for pending Notion memo input (reply to the save prompt)
    pending_memo = context.user_data.get("pending_memo")
    reply_to = update.message.reply_to_message
    if pending_memo and reply_to and reply_to.message_id == pending_memo.get("prompt_message_id"):
        memo_text = update.message.text.strip()
        if memo_text:
            notion = get_notion()
            success = await notion.update_memo(pending_memo["page_id"], memo_text)
            if success:
                context.user_data.pop("pending_memo", None)
                await update.message.reply_text("메모가 저장됐어요 😊")
            else:
                await update.message.reply_text("메모 저장에 실패했어요 😥 잠시 후 다시 시도해 주세요!")
        return

    query = update.message.text.strip()
    if not query:
        return

    claude = get_claude()
    history = await sheets.get_history(limit=5)
    profile = await sheets.get_profile()
    try:
        answer = await claude.answer_natural_language(query, history, profile)
    except Exception as e:
        logger.error("Natural language response failed: %s", e)
        await update.message.reply_text("일시적인 오류가 발생했어요 😥 잠시 후 다시 시도해 주세요!")
        return

    parts = utils.split_message(answer)
    for part in parts:
        await update.message.reply_text(part)

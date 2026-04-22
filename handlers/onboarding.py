from __future__ import annotations
import logging

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from handlers.auth import (
    check_password,
    is_blocked,
    increment_attempts,
    reset_attempts,
    remaining_attempts,
    block_until,
    notify_admin_lockout,
)
from services.sheets import get_sheets
from services.claude import get_claude
import utils

logger = logging.getLogger(__name__)

# Conversation states
WAITING_PASSWORD = 0
Q1_JOB = 1
Q2_CAREER = 2
Q3_INDUSTRY = 3
Q4_PLATFORM = 4
Q5_GLOBAL = 5
Q6_EXTRA = 6
PROFILE_CONFIRM = 7

WELCOME = (
    "🐾 안녕하세요!\n\n"
    "저는 뭉치치예요 🐶\n"
    "매일 아침 마케팅 인사이트를 물어다 드릴게요!\n\n"
    "요일마다 다른 주제로 깊이 있는 브리핑을 보내드리고,\n"
    "궁금한 용어나 주제가 있으면 언제든지 물어봐 주세요 😊\n"
    "오늘도 좋은 하루 되세요!\n\n"
    "시작하기 전에 비밀번호를 입력해 주세요!"
)

QUESTIONS = {
    Q1_JOB: "1️⃣ 현재 어떤 마케팅 직무를 목표로 하고 계세요?\n(브랜드 마케터, 퍼포먼스 마케터, 콘텐츠 마케터 등 편하게 말씀해 주세요!)",
    Q2_CAREER: "2️⃣ 신입 준비 중이신가요, 아니면 경력직 이직을 준비 중이신가요?",
    Q3_INDUSTRY: "3️⃣ 특별히 관심 있는 업종이 있으신가요?\n(뷰티, 패션, 푸드, 테크 등 여러 개도 괜찮아요!)",
    Q4_PLATFORM: "4️⃣ 주로 어떤 플랫폼에 관심이 많으세요?\n(인스타그램, 유튜브, 틱톡, 네이버, 카카오 등)",
    Q5_GLOBAL: "5️⃣ 국내 위주로 볼까요, 글로벌 브랜드도 함께 볼까요?\n1. 국내 위주\n2. 글로벌도 함께\n3. 둘 다 비슷하게",
    Q6_EXTRA: "6️⃣ 브리핑에서 특별히 더 챙겨줬으면 하는 게 있으면 자유롭게 말씀해 주세요 😊\n(없으면 없다고 하셔도 됩니다!)",
}

ANSWER_KEYS = {
    Q1_JOB: "q1",
    Q2_CAREER: "q2",
    Q3_INDUSTRY: "q3",
    Q4_PLATFORM: "q4",
    Q5_GLOBAL: "q5",
    Q6_EXTRA: "q6",
}

NEXT_STATE = {
    Q1_JOB: Q2_CAREER,
    Q2_CAREER: Q3_INDUSTRY,
    Q3_INDUSTRY: Q4_PLATFORM,
    Q4_PLATFORM: Q5_GLOBAL,
    Q5_GLOBAL: Q6_EXTRA,
    Q6_EXTRA: PROFILE_CONFIRM,
}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    sheets = get_sheets()

    # 온보딩이 완료된 사용자_ChatID 본인만 이미 완료 처리 (관리자는 항상 비밀번호 흐름 진입)
    user_id = await sheets.get_setting("사용자_ChatID")
    if user_id and str(user_id).strip() and str(chat_id) == str(user_id).strip():
        profile = await sheets.get_profile()
        if str(profile.get("온보딩완료", "")).lower() == "true":
            await update.message.reply_text(
                "✅ 이미 설정이 완료됐어요! /help 로 명령어를 확인해 보세요 😊"
            )
            return ConversationHandler.END

    if is_blocked(chat_id):
        await update.message.reply_text(
            "🚫 비밀번호를 너무 많이 틀렸어요. 24시간 후에 다시 시도해 주세요."
        )
        return ConversationHandler.END

    await update.message.reply_text(WELCOME)
    return WAITING_PASSWORD


async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    if is_blocked(chat_id):
        await update.message.reply_text(
            "🚫 현재 차단된 상태예요. 24시간 후에 다시 시도해 주세요."
        )
        return ConversationHandler.END

    if await check_password(text):
        reset_attempts(chat_id)
        await update.message.reply_text("✅ 비밀번호 확인 완료!\n\n프로필을 설정할게요. 질문에 편하게 답해 주세요!")
        await update.message.reply_text(QUESTIONS[Q1_JOB])
        context.user_data["onboarding"] = {}
        return Q1_JOB
    else:
        attempts = increment_attempts(chat_id)
        remaining = remaining_attempts(chat_id)
        if remaining <= 0:
            block_until(chat_id)
            await notify_admin_lockout(context, chat_id)
            await update.message.reply_text(
                "🚫 비밀번호를 5번 틀렸어요. 24시간 동안 접근이 제한됩니다."
            )
            return ConversationHandler.END
        await update.message.reply_text(
            f"❌ 비밀번호가 틀렸어요. ({remaining}번 남았어요)"
        )
        return WAITING_PASSWORD


async def _handle_question(update: Update, context: ContextTypes.DEFAULT_TYPE, state: int) -> int:
    answer = update.message.text.strip()
    key = ANSWER_KEYS[state]
    context.user_data.setdefault("onboarding", {})[key] = answer

    next_state = NEXT_STATE[state]
    if next_state == PROFILE_CONFIRM:
        return await show_profile_confirm(update, context)
    else:
        await update.message.reply_text(QUESTIONS[next_state])
        return next_state


async def handle_q1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_question(update, context, Q1_JOB)


async def handle_q2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_question(update, context, Q2_CAREER)


async def handle_q3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_question(update, context, Q3_INDUSTRY)


async def handle_q4(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_question(update, context, Q4_PLATFORM)


async def handle_q5(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_question(update, context, Q5_GLOBAL)


async def handle_q6(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _handle_question(update, context, Q6_EXTRA)


async def show_profile_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answers = context.user_data.get("onboarding", {})
    claude = get_claude()
    try:
        parsed = await claude.parse_onboarding_answers(answers)
        context.user_data["parsed_profile"] = parsed
        summary = await claude.summarize_profile_for_confirm(parsed)
        await update.message.reply_text(summary)
    except Exception as e:
        logger.error("Profile parse error: %s", e)
        await update.message.reply_text(
            "⚠️ 잠시 오류가 있었어요. 다시 입력해 주세요!\n\n"
            f"- 목표직무: {answers.get('q1', '')}\n"
            f"- 경력수준: {answers.get('q2', '')}\n"
            f"- 관심업종: {answers.get('q3', '')}\n"
            f"- 관심플랫폼: {answers.get('q4', '')}\n"
            f"- 글로벌여부: {answers.get('q5', '')}\n"
            f"- 기타요청: {answers.get('q6', '')}\n\n"
            "맞으면 '네', 수정하려면 '아니요'를 입력해 주세요 😊"
        )
        context.user_data["parsed_profile"] = {
            "목표직무": answers.get("q1", ""),
            "경력수준": answers.get("q2", ""),
            "관심업종": answers.get("q3", ""),
            "관심플랫폼": answers.get("q4", ""),
            "글로벌여부": answers.get("q5", ""),
            "기타요청": answers.get("q6", ""),
        }
    return PROFILE_CONFIRM


async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().lower()
    chat_id = update.effective_chat.id

    positive = ["네", "맞아요", "맞아", "응", "예", "yes", "ok", "확인", "좋아요", "좋아"]
    negative = ["아니요", "아니", "no", "다시", "수정"]

    if any(p in text for p in positive):
        sheets = get_sheets()
        parsed = context.user_data.get("parsed_profile", {})
        now = utils.get_korea_now()

        profile_data = {
            **parsed,
            "관심업종비율": "60",
            "인접산업비율": "30",
            "전체트렌드비율": "10",
            "온보딩완료": "true",
            "온보딩날짜": utils.date_to_str(now),
        }
        await sheets.set_profile_bulk(profile_data)
        # 관리자가 테스트로 온보딩을 진행하더라도 사용자_ChatID를 덮어쓰지 않음
        if not await sheets.is_admin(chat_id):
            await sheets.set_setting("사용자_ChatID", str(chat_id))
        await sheets.set_setting("마지막_업데이트", utils.date_to_str(now))

        msg1 = (
            "🐾 뭉치치 사용 안내\n\n"
            "온보딩이 완료됐어요! 본격적으로 시작하기 전에 제가 어떻게 작동하는지 먼저 알려드릴게요 😊\n\n"
            "─────────────────\n"
            "📅 언제 메시지가 오나요?\n\n"
            "저는 매일 아침 8시에 메시지를 보내드려요. 단, 주말(토·일)과 공휴일은 발송하지 않아요. 푹 쉬셔야죠 😄\n\n"
            "매월 마지막 평일에는 그달 저장해두신 브리핑들을 모아서 \"이달의 인사이트 요약\"을 보내드려요. 한 달 동안 뭘 배웠는지 한눈에 돌아볼 수 있는 시간이에요.\n\n"
            "─────────────────\n"
            "📆 요일별로 어떤 내용이 오나요?\n\n"
            "월요일 📱 — 플랫폼 & 알고리즘 동향\n"
            "인스타그램, 유튜브, 틱톡, 네이버 등 주요 플랫폼이 알고리즘을 어떻게 바꿨는지, 마케터로서 어떻게 대응해야 하는지를 다뤄요. 한 주를 이 내용으로 시작하면 콘텐츠 전략을 그에 맞게 짤 수 있거든요.\n\n"
            "화요일 🎯 — 캠페인 & 브랜드 사례 분석\n"
            "최근 화제가 됐거나 성과가 좋았던 마케팅 캠페인을 하나 골라서 깊게 파고들어요. \"왜 이게 먹혔는지\", \"어떤 전략이었는지\"를 분석해서 실무에서 바로 써먹을 수 있는 인사이트로 전달해드려요.\n\n"
            "수요일 📊 — 데이터 & 리포트\n"
            "Nielsen, Meta, Google, 대형 광고대행사 등 공신력 있는 기관에서 발표한 리포트와 데이터를 요약해드려요. 면접이나 업무에서 바로 인용할 수 있는 수치들 위주로 골라드립니다.\n\n"
            "목요일 💼 — 커리어 & 스킬 / 퍼포먼스 마케팅 (격주 교체)\n"
            "격주로 바뀌어요. 한 주는 마케터 취업 시장 분석, 포트폴리오 팁, 요즘 채용 공고에서 요구하는 스킬을 다루고요. 다음 주는 퍼포먼스 마케팅 — ROAS, CTR, 메타/구글 광고 집행 전략 같은 실무 내용을 다뤄요.\n\n"
            "금요일 📰 — 주간 정리 + 읽을거리\n"
            "그 주 마케팅 업계에서 있었던 주요 소식을 짧게 정리하고, 주말에 여유 있게 읽어볼 만한 아티클이나 리포트 2-3개를 링크와 함께 추천해드려요."
        )
        msg2 = (
            "⚙️ 이런 기능들을 쓸 수 있어요!\n\n"
            "/alarm\n브리핑 받는 시간을 원하는 시간으로 바꿀 수 있어요.\n\n"
            "/briefing\n지금 바로 오늘의 브리핑을 받아볼 수 있어요.\n\n"
            "/feedback\n불편한 점이나 개선 아이디어를 개발자에게 전달할 수 있어요.\n\n"
            "/help\n사용할 수 있는 명령어 전체 목록을 볼 수 있어요.\n\n"
            "/pause\n브리핑을 잠시 멈추고 싶을 때 사용해요.\n\n"
            "/profile\n내 프로필과 브리핑 설정을 확인하고 수정 방법을 볼 수 있어요.\n\n"
            "/recap [날짜]\n날짜를 입력하면 그날 받은 브리핑을 다시 볼 수 있어요.\n예: /recap 저번 주 화요일\n\n"
            "/request [내용]\n브리핑에 반영할 요청사항을 저장해요. 다음 브리핑부터 바로 반영돼요.\n예: /request 브리핑 수준을 좀 더 높여줘\n\n"
            "/resume\n멈춰둔 브리핑을 다시 받고 싶을 때 사용해요.\n\n"
            "/schedule\n요일별 브리핑 내용을 확인할 수 있어요.\n\n"
            "/term [용어]\n마케팅 용어가 헷갈릴 때 입력하면 쉽게 설명해드려요.\n예: /term ROAS\n\n"
            "/topic [주제]\n궁금한 마케팅 주제를 입력하면 즉석에서 브리핑을 만들어드려요.\n예: /topic 인스타그램 릴스 전략\n\n"
            "/update [항목] [값]\n프로필 항목을 수정할 수 있어요.\n예: /update 목표직무 브랜드마케터\n\n"
            "내일 아침 8시에 첫 브리핑으로 찾아올게요 🐾"
        )
        await update.message.reply_text(msg1)
        await update.message.reply_text(msg2)
        context.user_data.pop("onboarding", None)
        context.user_data.pop("parsed_profile", None)
        return ConversationHandler.END
    elif any(n in text for n in negative):
        await update.message.reply_text(
            "다시 처음부터 설정할게요! 😊\n\n" + QUESTIONS[Q1_JOB]
        )
        context.user_data["onboarding"] = {}
        return Q1_JOB
    else:
        await update.message.reply_text(
            "맞으면 '네', 수정하고 싶으면 '아니요'라고 입력해 주세요 😊"
        )
        return PROFILE_CONFIRM


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("설정을 취소했어요. /start 로 다시 시작할 수 있어요!")
    context.user_data.pop("onboarding", None)
    context.user_data.pop("parsed_profile", None)
    return ConversationHandler.END


def get_onboarding_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            WAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password)],
            Q1_JOB: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_q1)],
            Q2_CAREER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_q2)],
            Q3_INDUSTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_q3)],
            Q4_PLATFORM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_q4)],
            Q5_GLOBAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_q5)],
            Q6_EXTRA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_q6)],
            PROFILE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        name="onboarding",
        persistent=False,
    )

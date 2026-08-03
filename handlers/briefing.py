from __future__ import annotations
import logging

from telegram import Update
from telegram.ext import ContextTypes

from handlers.auth import require_auth
from services.sheets import get_sheets
from services.notion import get_notion
from services.claude import get_claude
from services.briefing import (
    create_and_send_briefing,
    generate_with_search_verification,
    parse_briefing_sections,
    strip_markdown,
)
import utils
import config

logger = logging.getLogger(__name__)


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    chat_id = update.effective_chat.id
    await update.message.reply_text("📬 브리핑을 가져오는 중이에요... 잠깐만요!")
    try:
        success = await create_and_send_briefing(context, chat_id)
    except Exception as e:
        logger.error("Unhandled error in create_and_send_briefing: %s", e)
        success = False
    if not success:
        await update.message.reply_text("일시적인 오류가 발생했어요 😥 잠시 후 다시 시도해 주세요!")


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    await update.message.reply_text(
        "📆 요일별 브리핑 안내\n\n"
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


async def cmd_recap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "날짜 또는 표현을 입력해 주세요!\n예: /recap 2025-04-01\n예: /recap 저번 주 금요일"
        )
        return

    query = " ".join(args)
    now = utils.get_korea_now()
    today_str = utils.date_to_str(now)

    # Try to parse date
    claude = get_claude()
    sheets = get_sheets()

    try:
        date_str = await claude.parse_date_expression(query, today_str)
        # Validate format YYYY-MM-DD
        import re
        if not re.match(r"\d{4}-\d{2}-\d{2}", date_str):
            raise ValueError("Invalid date format")
        date_str = date_str[:10]
    except Exception:
        await update.message.reply_text(
            "날짜를 이해하지 못했어요 😥\n다음 형식으로 입력해 주세요: /recap 2025-04-01"
        )
        return

    history_row = await sheets.get_history_by_date(date_str)
    if not history_row:
        await update.message.reply_text(
            f"📭 {date_str} 날짜의 브리핑 이력이 없어요.\n다른 날짜를 시도해 보세요!"
        )
        return

    theme = history_row.get("요일테마", "")
    sources = history_row.get("소스링크", "")
    await update.message.reply_text(
        f"📅 {date_str} 브리핑 요약\n\n"
        f"테마: {theme}\n"
        f"발송여부: {history_row.get('발송여부', '')}\n"
        f"노션저장: {history_row.get('노션저장여부', '')}\n\n"
        f"📎 소스 링크:\n{sources or '없음'}"
    )


async def cmd_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "주제를 입력해 주세요!\n예: /topic 인플루언서 마케팅"
        )
        return

    topic = " ".join(args)
    claude = get_claude()

    # Check if marketing-related (cheap model)
    is_marketing = await claude.is_marketing_topic(topic)
    if not is_marketing:
        await update.message.reply_text(
            "마케팅 관련 주제만 요청할 수 있어요 😊\n다른 주제를 시도해 보세요!"
        )
        return

    await update.message.reply_text(f"📌 {topic} 브리핑을 작성 중이에요...")
    sheets = get_sheets()
    profile = await sheets.get_profile()
    verified = await generate_with_search_verification(
        lambda: claude.generate_topic_briefing(topic, profile, max_uses=2),
        label=f"/topic 브리핑({topic})",
    )
    if verified is None:
        await update.message.reply_text(
            "지금은 확인된 자료를 찾지 못했어요. 잠시 후 다시 시도해 주세요"
        )
        return

    briefing = strip_markdown(verified.text)
    parts = utils.split_message(briefing)
    for part in parts:
        await update.message.reply_text(part)

    # /topic으로 받은 브리핑도 이력에 저장 → 정기 브리핑이 같은 토픽 회피하도록
    try:
        now = utils.get_korea_now()
        date_str = utils.date_to_str(now)
        sources = verified.sources
        sections = parse_briefing_sections(briefing)
        content_sections = sections[1:-1] if len(sections) >= 3 else sections

        try:
            keywords = await claude.extract_keywords(briefing)
        except Exception as e:
            logger.error("Topic briefing keyword extraction failed: %s", e)
            keywords = []

        try:
            pools = await claude.classify_briefing_pools(content_sections)
        except Exception as e:
            logger.error("Topic briefing pool classification failed: %s", e)
            pools = ["T"] * len(content_sections)

        await sheets.add_history(
            date_str,
            f"/topic: {topic}",
            True,
            sources,
            False,
            keywords=keywords,
            pools=pools,
        )
    except Exception as e:
        logger.error("Failed to save /topic history: %s", e)
        # 발송은 이미 성공했으니 사용자에겐 알리지 않음


async def cmd_term(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_auth(update, context):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "용어를 입력해 주세요!\n예: /term ROAS"
        )
        return

    term = " ".join(args)
    claude = get_claude()
    sheets = get_sheets()
    profile = await sheets.get_profile()

    try:
        explanation = await claude.explain_term(term, profile)
    except Exception as e:
        logger.error("Term explanation failed: %s", e)
        await update.message.reply_text("일시적인 오류가 발생했어요 😥 잠시 후 다시 시도해 주세요!")
        return

    parts = utils.split_message(strip_markdown(explanation))
    for part in parts:
        await update.message.reply_text(part)

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from services.sheets import get_sheets
from services.claude import BriefingResult, get_claude
import utils
import config

logger = logging.getLogger(__name__)

SECTION_SEP_RE = re.compile(r"^━{5,}$", re.MULTILINE)
URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]]+")
# 줄 앞머리의 불릿/번호 마커만 제거 (본문 숫자는 건드리지 않음)
LIST_MARKER_RE = re.compile(r"^[\s•\-–—*]*(?:\d+[.)]\s*)?")
_URL_TRAILING_PUNCT = ".,;:!?)]}>\"'…、。」』"
TRAILING_SEP_RE = re.compile(r"[\s—–\-|]+$")
# 항목 말미의 "📌 출처: 기관명" 줄. 마지막 "📎 참고 출처 전체 목록" 헤더와는 구분된다
ITEM_SOURCE_RE = re.compile(r"출처\s*[:：]")

# 최초 1회 + 재생성 2회
MAX_BRIEFING_ATTEMPTS = 3


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
    """참고 출처 섹션에서 실제 URL이 들어있는 줄만 뽑는다.

    웹 검색으로 받은 진짜 URL만 이력·중복 체크에 쓰기 위해 URL이 없는 줄은 버린다.
    모델이 출력 형식을 벗어나 섹션에서 아무것도 못 뽑으면 본문 전체에서 URL만 훑는다.
    """
    lines = text.split("\n")
    sources: List[str] = []
    in_source_section = False
    for line in lines:
        line = line.strip()
        if "참고 출처" in line:
            in_source_section = True
            continue
        if in_source_section:
            if line.startswith("※") or (line.startswith("━") and len(line) > 5):
                break
            if not line or line.startswith("━") or line.startswith("─"):
                continue
            cleaned = LIST_MARKER_RE.sub("", line).strip()
            # URL이 없는 줄(기억으로 적은 기관명 등)은 출처로 인정하지 않는다
            if not cleaned or not URL_RE.search(cleaned):
                continue
            # 시트에 "|"로 join/split 되므로 파이프는 인코딩해서 보관
            sources.append(cleaned.replace("|", "%7C"))

    if not sources:
        sources = URL_RE.findall(text)

    seen = set()
    unique: List[str] = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique


def strip_source_block(section: str) -> str:
    """항목 안에 끼어든 '참고 출처' 목록을 잘라낸다.

    모델이 항목마다 출처 목록을 반복해서 붙이면 같은 링크 묶음이 여러 번 발송된다.
    출처 목록은 항상 섹션 끝에 오므로 해당 줄부터 끝까지 버린다.
    """
    lines = section.split("\n")
    for i, line in enumerate(lines):
        if "참고 출처" in line:
            return "\n".join(lines[:i]).rstrip()
    return section


def check_item_sources(text: str, sources: List[str]) -> Optional[str]:
    """항목마다 출처가 붙어 있는지 검사한다. 문제가 있으면 사유, 없으면 None.

    검사 항목
      1) 각 항목 섹션에 '출처:' 줄이 있는지
      2) 참고 출처 목록의 URL 개수가 항목 수 이상인지

    기관명과 URL을 직접 연결하는 검사는 넣지 않았다. 검색 결과에서 뽑아둔 것은 URL뿐이고,
    '매일경제 → mk.co.kr'처럼 한국 매체명은 도메인과 문자열이 겹치지 않아 오탐이 난다.
    오탐은 곧 발송 실패이므로 지시받은 최소 조건(1, 2)만 강제한다.
    """
    sections = parse_briefing_sections(text)
    if len(sections) < 3:
        # 형식이 예상과 다르면 판정하지 않고 전체 URL 검증에 맡긴다
        return None

    items = [strip_source_block(s) for s in sections[1:-1]]
    items = [s for s in items if s.strip()]
    if not items:
        return "항목 섹션이 없음"

    missing = [i + 1 for i, s in enumerate(items) if not ITEM_SOURCE_RE.search(s)]
    if missing:
        return f"출처 줄이 없는 항목 {missing}"

    if len(sources) < len(items):
        return f"검증된 URL {len(sources)}개 < 항목 {len(items)}개"

    return None


def _url_keys(url: str) -> set:
    """URL 비교용 키. 꼬리 문장부호·앵커·마지막 슬래시를 떼고, 쿼리 유무 둘 다 허용한다."""
    u = url.strip().rstrip(_URL_TRAILING_PUNCT)
    u = u.split("#", 1)[0].rstrip("/").lower()
    return {u, u.split("?", 1)[0].rstrip("/")}


def sanitize_sources(text: str, searched_urls: List[str]) -> Tuple[str, List[str]]:
    """검색으로 실제 확인된 URL만 남기고 나머지는 본문에서 제거한다.

    URL이 전부 제거된 줄은 '기관명 —' 껍데기만 남으므로 줄째로 버린다.
    반환값은 (정리된 본문, 제거된 URL 목록).
    """
    allowed: set = set()
    for u in searched_urls:
        allowed |= _url_keys(u)

    removed: List[str] = []
    kept_lines: List[str] = []
    for line in text.split("\n"):
        found = URL_RE.findall(line)
        if not found:
            kept_lines.append(line)
            continue

        new_line = line
        line_removed = [u for u in found if not (_url_keys(u) & allowed)]
        if not line_removed:
            kept_lines.append(line)
            continue

        for url in line_removed:
            new_line = new_line.replace(url, "")
        removed.extend(line_removed)

        if URL_RE.search(new_line):
            kept_lines.append(TRAILING_SEP_RE.sub("", new_line))
        # 남은 URL이 없으면 그 줄은 통째로 버린다

    return "\n".join(kept_lines), removed


@dataclass
class VerifiedBriefing:
    """검색 근거 검증을 통과한 브리핑."""

    text: str
    sources: List[str] = field(default_factory=list)
    search_uses: int = 0
    attempts: int = 1


async def generate_with_search_verification(
    generate: Callable[[], Awaitable[BriefingResult]],
    *,
    label: str,
    max_attempts: int = MAX_BRIEFING_ATTEMPTS,
    require_item_sources: bool = False,
) -> Optional[VerifiedBriefing]:
    """웹검색 근거가 확인될 때까지 재생성하는 공통 루프.

    정기 브리핑과 /topic이 같은 검증 절차를 쓴다. 호출부별 차이(모델 메서드, max_uses,
    시도 횟수, 로그 라벨)는 인자로 받고, 실패 시 사용자 안내는 호출부가 담당한다.

    재생성 조건: 예외 / 빈 본문 / 웹검색 0회 / stop_reason == "pause_turn" /
    검색으로 확인되지 않은 URL을 걷어낸 뒤 남은 출처 0개.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            result = await generate()
        except Exception as e:
            logger.error("%s 생성 실패 (시도 %d/%d): %s", label, attempt, max_attempts, e)
            continue

        logger.info(
            "%s 생성 시도 %d/%d — 웹검색 %d회, stop_reason=%s, 검색URL %d개, 본문 %d자",
            label, attempt, max_attempts,
            result.search_uses, result.stop_reason,
            len(result.searched_urls), len(result.text or ""),
        )

        if not result.text or not result.text.strip():
            logger.warning("%s: 빈 응답 (시도 %d/%d) — 재생성", label, attempt, max_attempts)
            continue

        if result.search_uses == 0:
            logger.warning(
                "%s: 웹검색이 한 번도 실행되지 않음 (시도 %d/%d) — 재생성",
                label, attempt, max_attempts,
            )
            continue

        if result.stop_reason == "pause_turn":
            logger.warning(
                "%s: 검색 루프가 pause_turn으로 중단됨 (시도 %d/%d) — 재생성",
                label, attempt, max_attempts,
            )
            continue

        cleaned_text, removed_urls = sanitize_sources(result.text, result.searched_urls)
        if removed_urls:
            logger.warning(
                "%s: 검색으로 확인되지 않은 URL %d개 제거 (시도 %d/%d): %s",
                label, len(removed_urls), attempt, max_attempts, ", ".join(removed_urls[:5]),
            )

        sources = extract_sources(cleaned_text)
        if not sources:
            logger.warning(
                "%s: 검증 후 남은 출처가 0개 (시도 %d/%d) — 폐기 후 재생성",
                label, attempt, max_attempts,
            )
            continue

        if require_item_sources:
            problem = check_item_sources(cleaned_text, sources)
            if problem:
                logger.warning(
                    "%s: 항목별 출처 검사 실패 — %s (시도 %d/%d) — 폐기 후 재생성",
                    label, problem, attempt, max_attempts,
                )
                continue

        logger.info(
            "%s 검증 통과 (시도 %d/%d) — 웹검색 %d회, 검증된 출처 %d개",
            label, attempt, max_attempts, result.search_uses, len(sources),
        )
        return VerifiedBriefing(
            text=cleaned_text,
            sources=sources,
            search_uses=result.search_uses,
            attempts=attempt,
        )

    logger.error("%s 생성 %d회 시도 모두 실패", label, max_attempts)
    return None


async def _generate_briefing_data(date_str: str, theme: str, weekday_ko: str) -> Optional[dict]:
    """검증을 통과할 때까지 브리핑을 생성하고, 키워드 추출·영역 분류까지 한 번에 처리한다."""
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
        blocked_entities = await sheets.get_blocked_entities()
    except Exception as e:
        logger.error("Failed to load recent history from sheets: %s", e)
        recent_sources = []
        blocked = {"strict": [], "medium": []}
        blocked_entities = {}

    verified = await generate_with_search_verification(
        lambda: claude.generate_briefing(
            profile, theme, date_str, weekday_ko,
            recent_sources=recent_sources,
            blocked_keywords_strict=blocked.get("strict", []),
            blocked_keywords_medium=blocked.get("medium", []),
            blocked_entities=blocked_entities,
            max_uses=3,
        ),
        label="정기 브리핑",
        require_item_sources=True,
    )
    if verified is None:
        logger.error("정기 브리핑 발송하지 않음 (검증 실패)")
        return None

    briefing_text = verified.text
    sources = verified.sources
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

    try:
        entities = await claude.extract_entities(briefing_text)
    except Exception as e:
        logger.error("Entity extraction failed: %s", e)
        entities = []

    return {
        "briefing_text": briefing_text,
        "sections": sections,
        "sources": sources,
        "keywords": keywords,
        "pools": pools,
        "entities": entities,
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
        # 항목마다 반복된 출처 목록은 제거한다.
        # 출처 목록 자체는 메시지로 보내지 않고 📎 소스 보기 버튼으로만 확인한다.
        content_sections = [strip_source_block(s) for s in sections[1:-1]]
        content_sections = [s for s in content_sections if s.strip()]

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
            entities=briefing_data.get("entities", []),
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
                entities=briefing_data.get("entities", []),
            )
        except Exception as e:
            logger.error("Sheets history save failed: %s", e)

    return successes, failures

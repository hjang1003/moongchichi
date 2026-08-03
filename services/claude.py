from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

import anthropic

import config

logger = logging.getLogger(__name__)


# Anthropic 서버 사이드 웹 검색 도구.
# _20260209 버전은 동적 필터링이 내장돼 있고 그게 내부적으로 코드 실행을 돌려서 느리다.
# 동적 필터링만 끄는 파라미터는 없으므로(allowed_callers는 커스텀 도구용이라 여기 안 먹는다)
# 동적 필터링이 없는 기본 버전으로 내려서 지연을 줄인다.
# 결과 블록 타입은 두 버전 모두 web_search_tool_result라 검증 로직은 그대로 동작한다.
WEB_SEARCH_TOOL_TYPE = "web_search_20250305"

# 검색이 붙은 브리핑 호출용 타임아웃(초). SDK 기본값은 10분이라 너무 길다.
# max_retries=0과 함께 써야 한다. SDK가 타임아웃을 자체 재시도하면
# 실제 대기 시간이 타임아웃 × (max_retries+1)로 늘어나기 때문.
# 재시도는 services/briefing.py의 검증 루프가 담당한다.
REQUEST_TIMEOUT_SEC = 180.0


# 검색 단계에서 아예 걷어낼 저품질 출처 도메인.
# 커뮤니티, 개인 블로그 플랫폼, 채용 사이트 게시판은 마케팅 브리핑 출처로 부적합하다.
# 프롬프트로 막으면 모델이 이미 읽은 뒤라 통계가 새어 들어오므로 검색 단계에서 차단한다.
BLOCKED_SOURCE_DOMAINS = [
    "winspec.co.kr",
    "community.linkareer.com",
    "linkareer.com",
    "blog.naver.com",
    "m.blog.naver.com",
    "cafe.naver.com",
    "tistory.com",
    "brunch.co.kr",
    "velog.io",
    "dcinside.com",
    "fmkorea.com",
    "ppomppu.co.kr",
    "clien.net",
    "ruliweb.com",
    "jobkorea.co.kr",
    "saramin.co.kr",
    "worldreview1989.com",
    "blogspot.com",
    "wordpress.com",
    "medium.com",
    "wixsite.com",
    "weebly.com",
    "squarespace.com",
]


def _web_search_tool(max_uses: int, blocked_domains: Optional[List[str]] = None) -> Dict:
    return {
        "type": WEB_SEARCH_TOOL_TYPE,
        "name": "web_search",
        "max_uses": max_uses,
        "blocked_domains": list(
            BLOCKED_SOURCE_DOMAINS if blocked_domains is None else blocked_domains
        ),
    }


# 검색으로 확인되지 않은 내용을 지어내지 못하게 막는 공통 규칙.
# system / user 프롬프트 양쪽에 넣는다.
SEARCH_GROUNDING_RULES = (
    "[검색 기반 작성 규칙 — 위반 시 답변 전체가 거부된다]\n"
    "1. 반드시 web_search 도구로 검색해서 확인한 내용만 작성하라. "
    "검색하지 않고 기억에 의존해 작성하는 것은 절대 금지다. 먼저 검색하고, 그 다음에 작성하라.\n"
    "2. 검색으로 확인하지 못한 수치는 절대 쓰지 마라. "
    "배수, 퍼센트, 증감률, 금액, 순위, 점유율, 사용자 수 전부 해당된다. "
    "확인하지 못했으면 수치를 빼고 문장을 서술하라. 어림값이나 '약 ~배' 같은 표현으로 얼버무리는 것도 금지다.\n"
    "3. 출처는 검색으로 실제 확인한 페이지만 적어라. "
    "기억이나 추측으로 기관명·미디어명을 적는 것은 금지다. "
    "참고 출처 목록에는 검색 결과에 실제로 등장한 URL만 그대로 옮겨 적어라. URL을 임의로 만들거나 변형하지 마라.\n"
    "4. 캠페인명, 브랜드명, 인물명, 이론 창시자는 검색으로 확인한 것만 언급하라. "
    "확인되지 않은 이름은 아예 쓰지 마라.\n"
    "5. 마케팅 프레임워크나 고전 이론을 다룰 때도 창시자와 발표 연도를 반드시 검색으로 확인하라. "
    "확인하지 못했다면 창시자와 연도를 쓰지 말고 개념만 서술하라.\n"
    "6. 확인된 정보가 부족하면 해당 항목을 짧게 쓰거나 다른 소재로 바꿔라. "
    "지어내서 분량을 채우는 것은 절대 금지다. 분량보다 사실 정확성이 항상 우선이다.\n"
    "7. 출처로 인정되는 것은 언론사, 공식 기관·협회, 업계 전문 미디어, 브랜드 공식 발표뿐이다. "
    "커뮤니티 게시판, 자유게시판, 취업 커뮤니티, 개인 블로그, 카페 글은 출처로 쓰지 마라. "
    "주제와 무관한 분야의 매체도 쓰지 마라 (예: 육아 매체를 홈리빙 소재의 출처로 쓰는 것). "
    "커뮤니티 글에 적힌 통계를 그대로 옮기지 마라. 원 출처를 검색으로 찾아 확인하고, "
    "원 출처를 못 찾으면 그 수치는 아예 쓰지 마라.\n"
    "8. 최근 3개월 이내에 발행된 자료를 우선해서 검색하라. "
    "3개월보다 오래된 자료를 쓸 때는 본문에 발행 시점을 밝혀라 (예: 2026년 2월 기준). "
    "6개월 이상 지난 자료는 최신 트렌드를 다루는 항목에 쓰지 마라. "
    "고전 캠페인 사례나 마케팅 프레임워크 설명에는 오래된 자료를 써도 된다.\n"
    "9. 인용체 사용 금지. 검색으로 확인된 사실은 반드시 단정형으로 서술하라.\n"
    "   다음 표현은 절대 쓰지 마라: '~라고 해요', '~다고 해요', '~라고 하네요', "
    "'~라는 분석이에요', '~라는 진단이 나왔어요', '~는 소식이 있어요', '~에 따르면'.\n"
    "   나쁜 예: '2026년 채용 트렌드는 팀핏이라고 해요'\n"
    "   좋은 예: '2026년 채용 트렌드는 팀핏입니다'\n"
    "   나쁜 예: '아이보스 칼럼에 따르면 AI와 인간 판단력의 결합이 핵심으로 꼽혀요'\n"
    "   좋은 예: '2026년 콘텐츠 경쟁력은 AI의 효율성과 인간의 판단력이 결합된 정도에서 갈립니다'\n"
    "   단정형이면 해요체도 된다. '팀핏이에요'는 단정형이므로 허용된다. "
    "말투 지침(부드러운 구어체)은 그대로 유지하면서 인용체만 걷어내라.\n"
    "   출처 표시는 각 항목 끝의 📌 출처 줄과 마지막 참고 출처 목록이 담당한다. "
    "문장마다 출처를 붙이지 마라.\n"
    "   예외는 전망과 추정뿐이다. 아직 일어나지 않은 일이나 확정되지 않은 수치를 말할 때만 "
    "인용체를 써라. 이미 일어난 일, 발표된 수치, 확인된 사실에는 예외 없이 단정형을 쓴다.\n"
    "10. 검색 결과를 그대로 이어붙이지 마라. 읽고 이해한 내용을 자기 문장으로 요약해서 써라. "
    "같은 사실을 표현만 바꿔 두 번 쓰지 마라. 한 문단 안에서 같은 내용을 반복 서술하는 것은 금지다. "
    "여러 출처가 같은 내용을 말하면 한 번만 쓰고 출처를 함께 표기하라.\n"
)


@dataclass
class BriefingResult:
    """브리핑 본문 + 검증에 쓸 메타데이터."""

    text: str
    search_uses: int = 0
    stop_reason: Optional[str] = None
    searched_urls: List[str] = field(default_factory=list)


def _extract_text(message) -> str:
    """Join the text of every text block in the response. Returns "" if there is none."""
    parts = [
        block.text
        for block in (getattr(message, "content", None) or [])
        if getattr(block, "type", None) == "text"
    ]
    return "".join(parts)


def _iter_search_result_blocks(message) -> Iterator[object]:
    for block in (getattr(message, "content", None) or []):
        if getattr(block, "type", None) == "web_search_tool_result":
            yield block


def _search_result_items(block) -> Optional[List]:
    """성공한 검색 결과 리스트를 돌려준다. 에러 블록이면 None.

    web_search_tool_result의 content는 성공 시 결과 리스트, 실패 시 error_code를 가진
    객체 하나다. 이 차이로 실제 검색 성공 여부를 판별한다.
    """
    content = getattr(block, "content", None)
    return content if isinstance(content, list) else None


def _count_search_uses(message) -> int:
    """웹 검색이 실제로 실행되어 결과가 돌아온 횟수. 에러로 끝난 검색은 세지 않는다."""
    return sum(1 for b in _iter_search_result_blocks(message) if _search_result_items(b))


def _extract_searched_urls(message) -> List[str]:
    """검색 결과 블록에 실제로 등장한 URL 목록 (순서 유지, 중복 제거)."""
    urls: List[str] = []
    seen = set()
    for block in _iter_search_result_blocks(message):
        items = _search_result_items(block)
        if not items:
            continue
        for item in items:
            url = getattr(item, "url", None)
            if url is None and isinstance(item, dict):
                url = item.get("url")
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _trim_preamble(text: str, marker: str) -> str:
    """검색 도중 나온 서두 텍스트를 잘라낸다. marker가 없으면 원문 그대로 둔다."""
    idx = text.find(marker)
    if idx <= 0:
        return text
    return text[idx:]


class ClaudeService:
    def __init__(self):
        self._client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

    async def generate_briefing(
        self,
        profile: Dict[str, str],
        theme: str,
        date_str: str,
        weekday_ko: str,
        recent_sources: Optional[List[str]] = None,
        blocked_keywords_strict: Optional[List[str]] = None,
        blocked_keywords_medium: Optional[List[str]] = None,
        blocked_entities: Optional[Dict[str, List[str]]] = None,
        max_uses: int = 3,
    ) -> BriefingResult:
        industry_ratio = profile.get("관심업종비율", "60")
        adjacent_ratio = profile.get("인접산업비율", "30")
        trend_ratio = profile.get("전체트렌드비율", "10")

        system_prompt = (
            "당신은 마케팅 인사이트 전문 브리핑 작성 AI입니다.\n"
            "부드럽고 친근한 말투로 작성합니다. ~했어요, ~예요, ~고요 같은 구어체를 사용하세요.\n"
            "확인된 사실은 단정형으로 서술하세요. '~라고 해요', '~다고 하네요', '~에 따르면' 같은 "
            "인용체를 문장마다 반복하지 마세요. 전망이나 해석에만 인용체를 쓰세요.\n"
            "각 항목은 배경 → 핵심 내용 → 실무 포인트 순서로 자연스럽게 이어지는 문단으로 작성하세요.\n"
            "배경과 핵심 내용 사이, 핵심 내용과 실무 포인트 사이는 구분선(─────────────────────────────)으로 나눠주세요.\n"
            "실무 포인트 문단에는 이모지를 1~2개 포함하세요.\n"
            "** ** 마크다운 굵게 표시는 절대 사용하지 마세요.\n"
            "제목은 이모지 + 주제 형식으로 작성하고, 제목과 본문 사이에 빈 줄을 넣어주세요.\n"
            "문단 사이에는 빈 줄을 넣어 가독성을 확보하세요.\n"
            "각 항목 말미에는 출처 기관명만 간략히 표기합니다: 📌 출처: 기관명/미디어명\n"
            "실제 URL은 마지막 '📎 참고 출처 전체 목록' 섹션에만 포함합니다. 형식: - 기관명 — https://실제URL\n"
            "[출처 매체 기준]\n"
            "좋은 예 — 국내: 아이보스, OpenAds, 모비인사이드, 매일경제, 한국경제, 조선비즈, 전자신문, "
            "각 브랜드 공식 뉴스룸. 해외: Nielsen, Meta, Google, 대형 광고대행사, 주요 마케팅 전문 미디어.\n"
            "나쁜 예 — 커뮤니티 자유게시판, 취업 커뮤니티, 개인 블로그, 네이버 카페, 티스토리, "
            "채용 사이트 게시판.\n"
            "나쁜 예에 해당하는 출처는 절대 사용 금지. 좋은 예에 해당하는 매체만 출처로 인정한다.\n"
            "\n"
            + SEARCH_GROUNDING_RULES +
            "\n"
            "[콘텐츠 영역 — 항목 3개를 아래 세 영역에서 선택해 작성]\n"
            "\n"
            "영역 A: 최신 마케팅 트렌드 (우선순위 1, 비중 약 70%)\n"
            "- 최근 마케팅 동향, 캠페인 사례, 데이터 리포트, 플랫폼 변화 등 최신 정보\n"
            "- 단, 아래 [절대 금지 키워드]에 포함된 것은 절대 다루지 마라\n"
            "\n"
            "영역 B: 마케팅 기본기·고전 사례 (우선순위 2, 비중 약 20%)\n"
            "- 마케팅 프레임워크 (4P, STP, AARRR, AIDA, RACE, SWOT, 5 Forces 등)\n"
            "- 고전 캠페인 사례 (애플 1984, 나이키 Just Do It, 도브 Real Beauty, 코카콜라 Share a Coke 등)\n"
            "- 마케팅 학자·이론 (필립 코틀러, 세스 고딘, 잭 트라우트 등의 핵심 개념)\n"
            "- 소비자 행동·브랜딩·포지셔닝 이론\n"
            "- 신입 취준생의 면접·포트폴리오 준비에 유용한 기초 콘텐츠\n"
            "\n"
            "영역 C: 인접 산업·글로벌 사례 (우선순위 3, 비중 약 10%)\n"
            "- 관심 업종 외 인접 산업 마케팅 사례\n"
            "- 글로벌 마케팅 동향 (북미, 유럽, 동남아, 일본 등)\n"
            "\n"
            "[비율 목표 및 우선순위]\n"
            "- 기본 비율: 영역 A 약 70% / 영역 B 약 20% / 영역 C 약 10%\n"
            "- 영역 A 우선. 차단 키워드 회피하느라 부족하면 영역 B, C 비중 늘려도 됨\n"
            "- 단, 영역 A 비중을 0으로 만들지 마라. 최소 1개 항목은 영역 A에서 가져와라\n"
            "- 영역 B, C에서도 차단 키워드는 동일하게 적용된다\n"
            "\n"
            "[필수 행동 규칙]\n"
            "1. 항상 3개 항목 작성. '오늘 적합한 콘텐츠를 찾지 못했어요' 같은 메시지는 절대 출력하지 마라. 영역 B와 C가 항상 충분한 풀이므로 콘텐츠 부족은 발생할 수 없다.\n"
            "2. 차단 키워드 위반 절대 금지. 위반 시 답변 전체가 거부된다.\n"
            "3. 모든 항목은 마케팅 분야와 명확하게 연관성 있을 것."
        )

        if blocked_keywords_strict:
            system_prompt += (
                "\n\n[절대 금지 키워드 — 빨간 리스트]\n"
                "아래 키워드들은 최근 8주 내 2회 이상 등장한 핫키워드입니다. 절대 다루지 마라. "
                "위반 시 답변 전체가 거부된다:\n"
                + "\n".join(f"- {kw}" for kw in blocked_keywords_strict)
            )

        if blocked_keywords_medium:
            system_prompt += (
                "\n\n[절대 금지 키워드 — 노란 리스트]\n"
                "아래 키워드들은 최근 4주 내 1회 등장한 키워드입니다. 절대 다루지 마라. "
                "위반 시 답변 전체가 거부된다:\n"
                + "\n".join(f"- {kw}" for kw in blocked_keywords_medium)
            )

        if blocked_entities:
            # 이론·캠페인만 전면 금지다. 브랜드를 여기 넣으면 안 된다.
            # 브랜드에는 Warc·Nielsen 같은 출처 매체가 섞여 들어오는데,
            # 전면 금지하면 그 매체를 출처로도 못 쓰게 되어 인용할 곳이 말라버린다.
            hard_lines = []
            for etype in ("이론", "캠페인"):
                names = blocked_entities.get(etype) or []
                if names:
                    hard_lines.append(f"[{etype}] " + ", ".join(names))
            if hard_lines:
                system_prompt += (
                    "\n\n[절대 금지 고유명사]\n"
                    "아래 이름들은 최근 브리핑에서 이미 다룬 것이다. 오늘 브리핑에서는 절대 언급하지 마라. "
                    "제목, 본문, 실무 포인트, 출처 어디에도 쓰지 마라. "
                    "이 이름을 중심으로 한 항목은 물론이고 곁다리 예시로 끌어오는 것도 금지다. "
                    "위반 시 답변 전체가 거부된다:\n"
                    + "\n".join(hard_lines)
                )

            # 인물과 브랜드는 중심 소재로만 금지한다. 출처 인용과 스치는 언급은 허용이다.
            soft_lines = []
            for etype in ("인물", "브랜드"):
                names = blocked_entities.get(etype) or []
                if names:
                    soft_lines.append(f"[{etype}] " + ", ".join(names))
            if soft_lines:
                system_prompt += (
                    "\n\n[중심 소재 금지]\n"
                    "아래 이름들은 최근 브리핑에서 이미 다뤘다. "
                    "이것을 항목의 중심 소재로 삼는 것은 금지다. "
                    "이름이 제목에 들어가거나, 그 인물의 이론·저서·발언이나 그 브랜드의 사례가 "
                    "한 항목의 주된 내용이 되면 위반이다. 위반 시 답변 전체가 거부된다.\n"
                    "허용되는 것은 두 가지다. 첫째, 다른 주제를 설명하다가 근거나 예시로 "
                    "이름을 한 번 스쳐 언급하는 것. 둘째, 출처로 인용하는 것. "
                    "출처 표기와 참고 출처 목록에는 아무 제한 없이 써도 된다.\n"
                    "단 한 항목에서 두 문장 이상을 여기 있는 이름에 쓰지 마라:\n"
                    + "\n".join(soft_lines)
                )

        if recent_sources:
            system_prompt += (
                "\n\n[중복 출처 금지]\n"
                "아래 URL들은 최근 8주 내 이미 사용한 출처입니다. 절대 동일한 URL을 다시 사용하지 마라:\n"
                + "\n".join(f"- {url}" for url in recent_sources)
            )

        additional_requests = profile.get("추가요청사항", "")
        additional_section = f"\n- 추가 요청사항:\n{additional_requests}" if additional_requests else ""

        user_prompt = f"""오늘은 {date_str} ({weekday_ko})이며, 오늘의 브리핑 테마는 "{theme}"입니다.

작성을 시작하기 전에 web_search 도구로 먼저 검색하세요. 검색 없이 바로 작성하지 마세요.

{SEARCH_GROUNDING_RULES}
사용자 프로필:
- 목표 직무: {profile.get("목표직무", "마케터")}
- 경력 수준: {profile.get("경력수준", "신입")}
- 관심 업종: {profile.get("관심업종", "")}
- 관심 플랫폼: {profile.get("관심플랫폼", "")}
- 글로벌 여부: {profile.get("글로벌여부", "국내위주")}
- 기타 요청: {profile.get("기타요청", "없음")}{additional_section}

콘텐츠 비율: 관심 업종 {industry_ratio}% / 인접 산업 {adjacent_ratio}% / 전체 트렌드 {trend_ratio}%

━━━━━━━ 작성 규칙 ━━━━━━━

1. 독립적인 소식/사례/데이터를 3개 다룰 것
2. 각 항목 구성:
   - 이모지 + 주제 제목 (헤드라인)
   - 빈 줄
   - 구분선(─────────────────────────────)
   - 빈 줄
   - 배경 문단 (왜 지금 중요한지, 3~4문장)
   - 빈 줄
   - 핵심 내용 문단들 (구체적 수치·전략·사실, 4~6문장)
   - 빈 줄
   - 구분선(─────────────────────────────)
   - 빈 줄
   - 실무 포인트 문단 (이모지 포함, 2~3문장)
   - 빈 줄
   - [출처: 기관명/미디어명]
3. 국내 + 글로벌 소스 균형 있게 사용
4. 전체 분량: 3000자 내외. 단, 검색으로 확인된 내용이 부족하면 분량을 줄여라. 분량을 채우려고 지어내지 마라
5. ** ** 마크다운 굵게 표시 절대 금지
6. 각 항목의 [출처: 기관명]과 마지막 참고 출처 목록은 web_search 결과에 실제로 나온 페이지만 사용할 것
7. 참고 출처 전체 목록은 브리핑 맨 마지막에 딱 한 번만 작성할 것. 항목마다 반복하지 말 것
8. 커뮤니티·개인 블로그·카페 글은 출처 금지. 언론사, 공식 기관, 업계 전문 미디어, 브랜드 공식 발표만 사용
9. 최근 3개월 이내 자료 우선. 그보다 오래된 자료는 본문에 발행 시점을 명시할 것
10. 확인된 사실은 단정형으로 서술할 것. 인용체는 전망·해석에만 사용

━━━━━━━ 출력 형식 ━━━━━━━

📅 {date_str} ({weekday_ko}) | {theme}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔍 [항목 1 제목]

─────────────────────────────

[배경 문단]

[핵심 내용 문단]

─────────────────────────────

💡 [실무 포인트 문단]

📌 출처: 기관명

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔍 [항목 2 제목]

(동일 구조 반복)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔍 [항목 3 제목]

(동일 구조 반복)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📎 참고 출처 전체 목록
- 기관명 — https://검색결과에_실제로_나온_URL
- 기관명 — https://검색결과에_실제로_나온_URL
- 기관명 — https://검색결과에_실제로_나온_URL
(각 줄에 반드시 http로 시작하는 실제 URL을 하나씩 포함할 것. URL 없는 줄은 쓰지 마세요.)

※ 위 내용은 웹 검색으로 확인한 자료를 정리한 것입니다."""

        message = await self._client.with_options(
            timeout=REQUEST_TIMEOUT_SEC, max_retries=0
        ).messages.create(
            model=config.CLAUDE_MAIN_MODEL,
            max_tokens=12000,
            system=system_prompt,
            thinking={"type": "disabled"},
            tools=[_web_search_tool(max_uses=max_uses)],
            messages=[{"role": "user", "content": user_prompt}],
        )
        return BriefingResult(
            text=_trim_preamble(_extract_text(message), "📅"),
            search_uses=_count_search_uses(message),
            stop_reason=getattr(message, "stop_reason", None),
            searched_urls=_extract_searched_urls(message),
        )

    async def parse_onboarding_answers(self, answers: Dict[str, str]) -> Dict[str, str]:
        prompt = f"""사용자가 마케팅 봇 온보딩에서 아래와 같이 답변했습니다.
JSON 형식으로 파싱해서 반환해 주세요. 반드시 아래 키들만 포함하세요:

질문과 답변:
1. 목표직무: {answers.get("q1", "")}
2. 경력수준: {answers.get("q2", "")}
3. 관심업종: {answers.get("q3", "")}
4. 관심플랫폼: {answers.get("q4", "")}
5. 글로벌여부: {answers.get("q5", "")}
6. 기타요청: {answers.get("q6", "")}

반환 형식 (JSON만, 설명 없음):
{{
  "목표직무": "...",
  "경력수준": "신입" 또는 "경력",
  "관심업종": "...",
  "관심플랫폼": "...",
  "글로벌여부": "국내위주" 또는 "글로벌포함" 또는 "둘다",
  "기타요청": "..."
}}"""

        message = await self._client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        text = _extract_text(message).strip()
        # Extract JSON from response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        return {}

    async def explain_term(self, term: str, profile: Dict[str, str]) -> str:
        career = profile.get("경력수준", "신입")
        prompt = f"""마케팅 용어 "{term}"을 {career} 마케터 지망생에게 설명해 주세요.

말투 지침:
- 존댓말 사용. ~해요, ~예요, ~거든요, ~고요, ~네요, ~죠 같은 부드럽고 자연스러운 어미
- 마크다운 문법 절대 금지. **, ##, 표, 코드블록 전부 사용하지 마세요
- 소제목 레이블 없이 자연스러운 문단으로
- 이모지 1~2개만

구성 순서:
1. 용어 정의를 2~3문장으로 자연스럽게 설명
2. 실제 실무에서 어떻게 쓰이는지 예시로
3. 관련 지표나 공식이 있으면 간단히 덧붙여서

간결하고 실무적으로 도움이 되게 작성해 주세요."""

        message = await self._client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_text(message)

    async def generate_topic_briefing(
        self, topic: str, profile: Dict[str, str], max_uses: int = 2
    ) -> BriefingResult:
        career = profile.get("경력수준", "신입")
        system_prompt = (
            "당신은 마케팅 주제 브리핑 작성 AI입니다.\n"
            "부드럽고 친근한 존댓말로 작성합니다.\n"
            "[출처 매체 기준]\n"
            "좋은 예 — 국내: 아이보스, OpenAds, 모비인사이드, 매일경제, 한국경제, 조선비즈, 전자신문, "
            "각 브랜드 공식 뉴스룸. 해외: Nielsen, Meta, Google, 대형 광고대행사, 주요 마케팅 전문 미디어.\n"
            "나쁜 예 — 커뮤니티 자유게시판, 취업 커뮤니티, 개인 블로그, 네이버 카페, 티스토리, "
            "채용 사이트 게시판.\n"
            "나쁜 예에 해당하는 출처는 절대 사용 금지. 좋은 예에 해당하는 매체만 출처로 인정한다.\n"
            "\n"
            + SEARCH_GROUNDING_RULES
        )
        prompt = f"""마케팅 주제 "{topic}"에 대해 브리핑을 작성해 주세요.
대상: {career} 마케터 지망생 / 관심 분야: {profile.get("관심업종", "")}

작성을 시작하기 전에 web_search 도구로 먼저 검색하세요. 검색 없이 바로 작성하지 마세요.

{SEARCH_GROUNDING_RULES}
말투 지침:
- 존댓말 사용. ~해요, ~예요, ~거든요, ~고요, ~네요, ~죠 같은 부드럽고 자연스러운 어미
- 확인된 사실은 단정형으로 서술. '~라고 해요', '~다고 하네요', '~에 따르면'을 반복하지 마세요
- 전망이나 해석처럼 단정할 수 없는 내용에만 인용체 사용
- 마크다운 문법 절대 금지. **, ##, 표(|---|), 코드블록 전부 사용하지 마세요
- 섹션 구분은 ━━━ 구분선으로만
- 참고 출처 목록은 맨 마지막에 한 번만. 섹션마다 반복하지 마세요
- 각 섹션 제목은 이모지 + 주제 형식으로
- 자연스러운 문단으로 작성

출력 형식:
📌 {topic} 브리핑
━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔍 [섹션 1 제목]

[내용 문단]

📌 출처: 기관명

━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔍 [섹션 2 제목]

(동일 구조 반복, 총 3~4개 섹션)

━━━━━━━━━━━━━━━━━━━━━━━━━━━

📎 참고 출처
- 기관명 — https://검색결과에_실제로_나온_URL
(각 줄에 반드시 http로 시작하는 실제 URL을 하나씩 포함할 것. URL 없는 줄은 쓰지 마세요.)

※ 위 내용은 웹 검색으로 확인한 자료를 정리한 것입니다.

1500자 이상으로 풍부하게 작성해 주세요. 단, 검색으로 확인된 내용이 부족하면 분량을 줄이세요. 분량을 채우려고 지어내지 마세요."""

        message = await self._client.with_options(
            timeout=REQUEST_TIMEOUT_SEC, max_retries=0
        ).messages.create(
            model=config.CLAUDE_MAIN_MODEL,
            max_tokens=8000,
            system=system_prompt,
            thinking={"type": "disabled"},
            tools=[_web_search_tool(max_uses=max_uses)],
            messages=[{"role": "user", "content": prompt}],
        )
        return BriefingResult(
            text=_trim_preamble(_extract_text(message), "📌"),
            search_uses=_count_search_uses(message),
            stop_reason=getattr(message, "stop_reason", None),
            searched_urls=_extract_searched_urls(message),
        )

    async def extract_keywords(self, briefing_text: str) -> List[str]:
        prompt = (
            "아래 마케팅 브리핑에서 각 항목당 1~2개씩, 전체 최대 6개 키워드를 추출해 주세요.\n\n"
            "추출 기준:\n"
            "- 구체적인 툴명, 플랫폼명, 캠페인명, 지표명, 기능명만 추출\n"
            "- 좋은 예: GA4, 루커 스튜디오, 코호트 분석, 인스타그램 릴스 알고리즘, ROAS, 메타 광고, 올리브영 오늘드림\n"
            "- 나쁜 예: 데이터 분석, AI 시대, 커리어 역량, 브랜딩, 포트폴리오, T자형 인재, 통합 마케팅\n"
            "- 추상적인 개념어, 역량어, 방향성 표현은 절대 포함하지 마세요\n\n"
            "쉼표로 구분해서 한 줄로만 반환하세요. 키워드만, 설명 없이.\n\n"
            f"브리핑:\n{briefing_text[:3000]}"
        )
        message = await self._client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = _extract_text(message).strip()
        return [k.strip() for k in raw.split(",") if k.strip()]

    async def extract_entities(self, briefing_text: str) -> List[str]:
        """브리핑에 등장한 고유명사를 '유형:이름' 형식으로 추출한다.

        키워드 추출(extract_keywords)이 GA4·ROAS 같은 좁은 토큰만 뽑도록 튜닝돼 있어서
        인물·이론·브랜드·캠페인 단위 반복을 못 잡는다. 그 구멍을 메우는 용도다.
        """
        prompt = (
            "아래 마케팅 브리핑에서 실제로 언급된 고유명사를 유형과 함께 추출해 주세요.\n\n"
            "유형은 다음 4가지만 사용하세요: 인물, 이론, 브랜드, 캠페인\n"
            "- 인물: 마케팅 학자, 저자, 경영진 등 실존 인물 이름\n"
            "- 이론: 프레임워크, 개념, 저서에서 나온 이론 이름\n"
            "- 브랜드: 기업명, 제품 브랜드명, 플랫폼명\n"
            "- 캠페인: 특정 캠페인·광고 시리즈의 고유 명칭\n\n"
            "좋은 예: 세스 고딘, 필립 코틀러, 4P, AARRR, 퍼미션 마케팅, 보라색 소, "
            "이케아, 애플, 나이키, Just Do It, Real Beauty\n"
            "나쁜 예: 마케팅, 콘텐츠, 브랜딩, AI, 소비자, 캠페인, 트렌드 같은 일반명사는 절대 추출하지 마세요\n\n"
            "출력 형식은 '유형:이름'을 세로줄(|)로 이어붙인 한 줄입니다. 설명은 쓰지 마세요.\n"
            "예시 답변: 인물:세스 고딘|이론:퍼미션 마케팅|브랜드:이케아|캠페인:Just Do It\n"
            "해당하는 고유명사가 없으면 아무것도 쓰지 말고 빈 줄로 답하세요.\n\n"
            f"브리핑:\n{briefing_text[:3000]}"
        )
        message = await self._client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = _extract_text(message).strip()
        entities: List[str] = []
        seen = set()
        for token in raw.split("|"):
            token = token.strip()
            if not token or ":" not in token:
                continue
            etype, _, name = token.partition(":")
            etype, name = etype.strip(), name.strip()
            if etype not in ("인물", "이론", "브랜드", "캠페인") or not name:
                continue
            key = f"{etype}:{name}"
            if key not in seen:
                seen.add(key)
                entities.append(key)
        return entities

    async def classify_briefing_pools(self, sections: List[str]) -> List[str]:
        """Classify each section into T/F/G. Returns list of strings same length as sections."""
        if not sections:
            return []
        sections_text = "\n\n---\n\n".join(
            f"[항목 {i+1}]\n{s[:500]}" for i, s in enumerate(sections)
        )
        prompt = (
            "아래 마케팅 브리핑 각 항목을 세 영역 중 하나로 분류하세요.\n\n"
            "T = 최신 트렌드 (최근 마케팅 동향, 캠페인 사례, 데이터 리포트, 플랫폼 변화 등)\n"
            "F = 마케팅 기본기·고전 (4P/STP/AARRR 프레임워크, 고전 캠페인, 학자·이론)\n"
            "G = 인접·글로벌 (관심 업종 외 인접 산업, 해외 시장 사례)\n\n"
            "각 항목을 T/F/G 한 글자로만 분류해서, 순서대로 쉼표로 구분해 답하세요.\n"
            "예시 답변: T,F,T\n\n"
            f"항목 수: {len(sections)}개\n\n"
            f"브리핑 항목들:\n{sections_text}\n\n"
            "분류 (한 줄, 설명 없음):"
        )
        try:
            message = await self._client.messages.create(
                model=config.CLAUDE_FAST_MODEL,
                max_tokens=30,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = _extract_text(message).strip()
            parsed = [p.strip().upper() for p in raw.split(",") if p.strip()]
            parsed = [p for p in parsed if p in ("T", "F", "G")]
            while len(parsed) < len(sections):
                parsed.append("T")
            return parsed[:len(sections)]
        except Exception:
            return ["T"] * len(sections)

    async def is_marketing_topic(self, topic: str) -> bool:
        prompt = f'"{topic}"은 마케팅 관련 주제입니까? 예/아니오로만 답하세요.'
        message = await self._client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = _extract_text(message).strip().lower()
        return "예" in answer or "yes" in answer

    async def parse_date_expression(self, expression: str, today_str: str) -> str:
        prompt = f"""오늘 날짜: {today_str}
사용자 입력: "{expression}"
위 자연어 날짜 표현을 YYYY-MM-DD 형식으로 변환해 주세요. 날짜만 반환하세요."""

        message = await self._client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=20,
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_text(message).strip()

    async def answer_natural_language(
        self, query: str, history: List[Dict], profile: Dict[str, str]
    ) -> str:
        system_prompt = (
            "당신은 마케팅을 잘 아는 친절한 전문가예요.\n"
            "존댓말을 사용하세요. ~해요, ~예요, ~거든요, ~고요 같은 부드러운 어미를 써주세요.\n"
            "가끔 ~네요, ~죠 같은 편한 어미도 자연스럽게 섞어서 딱딱하지 않게 해주세요.\n"
            "마크다운 문법은 절대 사용하지 마세요. **, ##, 표(|---|), 코드블록 전부 금지예요.\n"
            "항목을 나열할 때는 줄바꿈으로 구분하세요.\n"
            "제목이나 소제목 없이 자연스러운 문단으로 써주세요.\n"
            "이모지는 전체 답변에서 1~2개만 사용하세요.\n"
            "답변은 간결하게. 핵심만 짚고 너무 길게 늘어놓지 마세요."
        )
        history_summary = "\n".join(
            [f"- {h.get('날짜', '')} ({h.get('요일테마', '')})" for h in history[-5:]]
        )
        prompt = f"""사용자 프로필:
- 목표직무: {profile.get("목표직무", "")}
- 관심업종: {profile.get("관심업종", "")}

최근 브리핑 이력:
{history_summary or "없음"}

사용자 질문: {query}"""

        message = await self._client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_text(message)

    async def generate_monthly_summary(self, briefings: List[Dict]) -> str:
        if not briefings:
            return "이번 달 저장된 브리핑이 없어요 😊"

        briefings_text = "\n\n".join(
            [f"[{b['date']} / {b['theme']}]\n{b['content'][:500]}" for b in briefings]
        )

        system_prompt = (
            "당신은 마케팅 인사이트 월간 요약 전문 AI입니다.\n"
            "따뜻하고 응원하는 톤으로 작성하세요.\n"
            "** ** 마크다운 굵게 표시는 절대 사용하지 마세요.\n"
            "# 및 ### 같은 마크다운 헤더 문법은 절대 사용하지 마세요.\n"
            "표(|---|) 문법은 절대 사용하지 마세요.\n"
            "━ 같은 구분선 문자는 절대 사용하지 마세요. 섹션 구분은 빈 줄과 이모지 제목만으로 하세요.\n"
            "제목은 이모지 + 주제 형식으로 작성하고, 문단 사이에는 빈 줄을 넣어 가독성을 확보하세요."
        )

        prompt = f"""이번 달 저장된 브리핑 {len(briefings)}개를 분석해서
"이달의 인사이트 요약"을 작성해 주세요.

저장된 브리핑:
{briefings_text}

[작성 규칙]
1. ** ** 마크다운 굵게 표시 절대 금지
2. # 및 ### 마크다운 헤더 절대 금지
3. 표(|---|) 문법 절대 금지
4. ━ 같은 구분선 문자 절대 금지 (섹션 구분은 빈 줄로만)
5. 따뜻하고 응원하는 톤 유지

[출력 형식]
🗓️ 이달의 마케팅 인사이트 요약

📌 이달의 핵심 키워드 (3~5개)
[키워드 목록]

🔍 주목할 만한 트렌드
[2~3가지 트렌드 분석]

💡 다음 달 챙겨볼 포인트
[앞으로 주목해야 할 내용]"""

        message = await self._client.messages.create(
            model=config.CLAUDE_MAIN_MODEL,
            max_tokens=4000,
            system=system_prompt,
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_text(message)

    async def summarize_profile_for_confirm(self, profile_data: Dict[str, str]) -> str:
        prompt = f"""사용자가 입력한 마케팅 봇 프로필을 아래 정보를 기반으로 친근하게 요약해 주세요.

프로필:
{profile_data}

형식:
🐾 이렇게 설정했어요!

목표 직무: ...
경력 수준: ...
관심 업종: ...
관심 플랫폼: ...
글로벌 여부: ...
기타 요청: ...

맞으면 "네" 또는 "맞아요", 수정하고 싶으면 "아니요"라고 입력해 주세요 😊"""

        message = await self._client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_text(message)


_claude_service: ClaudeService | None = None


def get_claude() -> ClaudeService:
    global _claude_service
    if _claude_service is None:
        _claude_service = ClaudeService()
    return _claude_service

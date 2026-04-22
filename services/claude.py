from __future__ import annotations
import logging
from typing import Dict, List, Optional

import anthropic

import config

logger = logging.getLogger(__name__)


class ClaudeService:
    def __init__(self):
        self._client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

    async def generate_briefing(self, profile: Dict[str, str], theme: str, date_str: str, weekday_ko: str) -> str:
        industry_ratio = profile.get("관심업종비율", "60")
        adjacent_ratio = profile.get("인접산업비율", "30")
        trend_ratio = profile.get("전체트렌드비율", "10")

        system_prompt = (
            "당신은 마케팅 인사이트 전문 브리핑 작성 AI입니다.\n"
            "부드럽고 친근한 말투로 작성합니다. ~했어요, ~예요, ~고요 같은 구어체를 사용하세요.\n"
            "각 항목은 배경 → 핵심 내용 → 실무 포인트 순서로 자연스럽게 이어지는 문단으로 작성하세요.\n"
            "배경과 핵심 내용 사이, 핵심 내용과 실무 포인트 사이는 구분선(─────────────────────────────)으로 나눠주세요.\n"
            "실무 포인트 문단에는 이모지를 1~2개 포함하세요.\n"
            "** ** 마크다운 굵게 표시는 절대 사용하지 마세요.\n"
            "제목은 이모지 + 주제 형식으로 작성하고, 제목과 본문 사이에 빈 줄을 넣어주세요.\n"
            "문단 사이에는 빈 줄을 넣어 가독성을 확보하세요.\n"
            "각 항목 말미에는 출처 기관명만 간략히 표기합니다: 📌 출처: 기관명/미디어명\n"
            "실제 URL은 마지막 '📎 참고 출처 전체 목록' 섹션에만 포함합니다. 형식: - 기관명 — https://실제URL\n"
            "공신력 있는 소스(Nielsen, Meta, Google, 대형 광고대행사, 주요 마케팅 미디어) 우선 사용.\n"
            "반드시 실존하는 페이지의 실제 URL을 작성하고, 가상의 URL은 절대 사용하지 마세요."
        )

        user_prompt = f"""오늘은 {date_str} ({weekday_ko})이며, 오늘의 브리핑 테마는 "{theme}"입니다.

사용자 프로필:
- 목표 직무: {profile.get("목표직무", "마케터")}
- 경력 수준: {profile.get("경력수준", "신입")}
- 관심 업종: {profile.get("관심업종", "")}
- 관심 플랫폼: {profile.get("관심플랫폼", "")}
- 글로벌 여부: {profile.get("글로벌여부", "국내위주")}
- 기타 요청: {profile.get("기타요청", "없음")}

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
4. 전체 분량: 3000자 내외
5. ** ** 마크다운 굵게 표시 절대 금지

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
- 기관명 — https://실제URL
- 기관명 — https://실제URL
- 기관명 — https://실제URL

※ 위 정보는 AI 학습 데이터 기반으로 수집된 내용으로, 실제 최신 수치와 다를 수 있습니다."""

        message = await self._client.messages.create(
            model=config.CLAUDE_MAIN_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text

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
        text = message.content[0].text.strip()
        # Extract JSON from response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        return {}

    async def explain_term(self, term: str, profile: Dict[str, str]) -> str:
        career = profile.get("경력수준", "신입")
        prompt = f"""마케팅 용어 "{term}"을 설명해 주세요.
대상: {career} 마케터 지망생
형식:
📖 {term}

[정의 - 2~3문장]

💡 실무 활용
[실제 사용 예시]

🔢 관련 지표 또는 공식 (있는 경우)
[공식/수치 예시]

설명은 친근하되 정확하게, 실무적으로 도움이 되게 작성하세요."""

        message = await self._client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    async def generate_topic_briefing(self, topic: str, profile: Dict[str, str]) -> str:
        career = profile.get("경력수준", "신입")
        prompt = f"""마케팅 주제 "{topic}"에 대해 브리핑을 작성해 주세요.
대상: {career} 마케터 지망생 / 관심 분야: {profile.get("관심업종", "")}

형식:
📌 {topic} 브리핑
━━━━━━━━━━━━━━━━━━━━━━━━━━━

[주요 내용 3~4개 섹션, 각 섹션마다 출처 명시]

📎 참고 출처
[소스 목록]

※ 위 정보는 AI 학습 데이터 기반으로 수집된 내용으로, 실제 최신 수치와 다를 수 있습니다.

풍부하게 작성하세요 (1500자 이상)."""

        message = await self._client.messages.create(
            model=config.CLAUDE_MAIN_MODEL,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    async def is_marketing_topic(self, topic: str) -> bool:
        prompt = f'"{topic}"은 마케팅 관련 주제입니까? 예/아니오로만 답하세요.'
        message = await self._client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = message.content[0].text.strip().lower()
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
        return message.content[0].text.strip()

    async def answer_natural_language(
        self, query: str, history: List[Dict], profile: Dict[str, str]
    ) -> str:
        history_summary = "\n".join(
            [f"- {h.get('날짜', '')} ({h.get('요일테마', '')})" for h in history[-5:]]
        )
        prompt = f"""사용자 프로필:
- 목표직무: {profile.get("목표직무", "")}
- 관심업종: {profile.get("관심업종", "")}

최근 브리핑 이력:
{history_summary or "없음"}

사용자 질문: {query}

마케팅 전문가로서 친근하게 답변해 주세요. 브리핑 이력을 참조해서 구체적으로 답변하세요."""

        message = await self._client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    async def generate_monthly_summary(self, briefings: List[Dict]) -> str:
        if not briefings:
            return "이번 달 저장된 브리핑이 없어요 😊"

        briefings_text = "\n\n".join(
            [f"[{b['date']} / {b['theme']}]\n{b['content'][:500]}" for b in briefings]
        )

        prompt = f"""이번 달 저장된 브리핑 {len(briefings)}개를 분석해서
"이달의 인사이트 요약"을 작성해 주세요.

저장된 브리핑:
{briefings_text}

형식:
🗓️ 이달의 마케팅 인사이트 요약
━━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 이달의 핵심 키워드 (3~5개)
[키워드 목록]

🔍 주목할 만한 트렌드
[2~3가지 트렌드 분석]

💡 다음 달 챙겨볼 포인트
[앞으로 주목해야 할 내용]

따뜻하고 응원하는 톤으로 작성하세요."""

        message = await self._client.messages.create(
            model=config.CLAUDE_MAIN_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

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
        return message.content[0].text


_claude_service: ClaudeService | None = None


def get_claude() -> ClaudeService:
    global _claude_service
    if _claude_service is None:
        _claude_service = ClaudeService()
    return _claude_service

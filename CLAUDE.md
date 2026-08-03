# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

## 프로젝트 규칙 (뭉치치)

텔레그램 마케팅 브리핑 봇. Python + Railway + Google Sheets + Notion + Anthropic API.
수신자는 두 명이다. 사용자(주 수신자)는 버튼 포함, 관리자는 같은 내용을 버튼 없이 미러로 받는다.
관리자용 별도 콘텐츠를 만들지 마라.

### 세션 시작 시 반드시 할 것
먼저 git pull을 받아 다른 컴퓨터에서 올린 변경을 가져와라. 이건 매 세션 해당된다.

### 반드시 지킬 것
- git push는 사용자가 명시적으로 승인한 뒤에만 한다. 커밋까지 하고 멈춰라.
- requirements.txt는 지시 없이 수정하지 마라. 패키지 버전 핀도 마찬가지다.
- 시키지 않은 파일은 열어보되 고치지 마라. 리팩터링, 정리, 포맷 변경 전부 금지.
- 설계 판단이 필요한 지점이 나오면 멈추고 물어봐라. 알아서 정하지 마라.
- 작업이 끝나면 무엇을 바꿨는지 한국어로 요약하고 git diff --stat을 보여준 뒤 멈춰라.

### 코드 수정 후 검증
윈도우:
.venv\Scripts\python -m py_compile [수정한 파일들]
$env:TELEGRAM_BOT_TOKEN="x"; $env:ANTHROPIC_API_KEY="x"; $env:GOOGLE_SHEETS_ID="x"; $env:GOOGLE_SERVICE_ACCOUNT_JSON="{}"; $env:NOTION_API_KEY="x"; .venv\Scripts\python -c "import main, services.claude, services.sheets, services.briefing; print('import OK')"

맥:
.venv/bin/python -m py_compile [수정한 파일들]
TELEGRAM_BOT_TOKEN=x ANTHROPIC_API_KEY=x GOOGLE_SHEETS_ID=x GOOGLE_SERVICE_ACCOUNT_JSON='{}' NOTION_API_KEY=x .venv/bin/python -c "import main, services.claude, services.sheets, services.briefing; print('import OK')"

### 과거에 사고가 났던 지점
- 텔레그램 메시지에 마크다운 금지. parse_mode도 쓰지 마라. 별표와 우물정자, 표 전부 깨진다.
- 스케줄러에 PTB의 run_daily를 쓰지 마라. run_repeating(interval=60) + 내부 시각 비교 방식이다.
- 버튼 콜백 데이터에는 chat_id와 대상 식별자를 반드시 포함해라. 빠지면 다른 사용자 메시지에 적용된다.
- gspread에서 읽은 값은 숫자처럼 보이면 int로 변환돼 온다. ID 계열은 str()로 감싸라.
- Google Sheets API는 분당 호출 제한이 있다. 읽기 횟수를 늘리는 변경은 하지 마라.
- 사용자에게 발송되는 메시지에 출력을 임의로 추가하지 마라. 출처 목록 같은 것도 지시 없이 붙이지 마라.
- 웹 검색 연동과 검색 실행 검증, 출처 진위 검사 로직은 지어내기를 막는 핵심이다. 지시 없이 건드리지 마라.

### 컴퓨터를 옮길 때
사용자가 다른 컴퓨터로 옮긴다고 하면, 작업이 끝나지 않았더라도 반드시 커밋하고 푸시해라.
커밋하지 않은 변경은 옮긴 컴퓨터에 따라오지 않는다.
미완성이면 커밋 메시지 끝에 "(작업 중)"을 붙여라.
.env와 .venv는 커밋 대상이 아니다. 새 컴퓨터에서는 requirements.txt로 패키지를 다시 설치해야 한다.

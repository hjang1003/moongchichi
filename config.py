import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_SHEETS_ID = os.environ["GOOGLE_SHEETS_ID"]
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
NOTION_API_KEY = os.environ["NOTION_API_KEY"]
NOTION_BRIEFING_DATABASE_ID = os.getenv("NOTION_BRIEFING_DATABASE_ID", "")
NOTION_SAVED_DATABASE_ID = os.getenv("NOTION_SAVED_DATABASE_ID", "")
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID", "")

KOREA_TZ = "Asia/Seoul"
BRIEFING_HOUR = 8
BRIEFING_MINUTE = 0
MAX_MESSAGE_LENGTH = 4096
MAX_PASSWORD_ATTEMPTS = 5
LOCKOUT_HOURS = 24

CLAUDE_MAIN_MODEL = "claude-sonnet-5"
CLAUDE_FAST_MODEL = "claude-haiku-4-5-20251001"

WEEKDAY_THEMES = {
    0: "플랫폼 & 알고리즘 동향",
    1: "캠페인 & 브랜드 사례 분석",
    2: "데이터 & 리포트",
    3: {"odd": "커리어 & 스킬", "even": "퍼포먼스 마케팅"},
    4: "주간 정리 + 읽을거리 추천",
}

WEEKDAY_KO = {0: "월요일", 1: "화요일", 2: "수요일", 3: "목요일", 4: "금요일", 5: "토요일", 6: "일요일"}

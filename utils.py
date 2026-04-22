from __future__ import annotations
import pytz
from datetime import datetime, date, timedelta
from typing import List
import config


def get_korea_now() -> datetime:
    tz = pytz.timezone(config.KOREA_TZ)
    return datetime.now(tz)


def get_weekday_theme(dt: datetime | date) -> str:
    weekday = dt.weekday()
    if weekday == 3:
        week_num = dt.isocalendar()[1]
        return config.WEEKDAY_THEMES[3]["odd"] if week_num % 2 == 1 else config.WEEKDAY_THEMES[3]["even"]
    return config.WEEKDAY_THEMES.get(weekday, "")


def is_weekday(dt: datetime | date) -> bool:
    return dt.weekday() < 5


def is_last_weekday_of_month(dt: datetime | date) -> bool:
    if not is_weekday(dt):
        return False
    # Check if adding 1-7 days stays in the same month and is still a weekday
    for delta in range(1, 8):
        next_day = dt + timedelta(days=delta)
        if next_day.month != dt.month:
            break
        if is_weekday(next_day):
            return False
    return True


def get_last_weekday_of_month(year: int, month: int) -> date:
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    while last_day.weekday() >= 5:
        last_day -= timedelta(days=1)
    return last_day


def split_message(text: str, max_length: int = config.MAX_MESSAGE_LENGTH) -> List[str]:
    if len(text) <= max_length:
        return [text]

    parts = []
    paragraphs = text.split("\n\n")
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_length:
            current = current + "\n\n" + para if current else para
        else:
            if current:
                parts.append(current.strip())
            if len(para) > max_length:
                # Force split long paragraph by lines
                lines = para.split("\n")
                current = ""
                for line in lines:
                    if len(current) + len(line) + 1 <= max_length:
                        current = current + "\n" + line if current else line
                    else:
                        if current:
                            parts.append(current.strip())
                        current = line
            else:
                current = para

    if current:
        parts.append(current.strip())

    return parts


def date_to_str(dt: datetime | date) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d")


def format_date_korean(dt: datetime | date) -> str:
    weekday = config.WEEKDAY_KO[dt.weekday()]
    return dt.strftime(f"%Y년 %m월 %d일 ({weekday})")

import calendar
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Tuple


_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

_ISO_DAY_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_ISO_MONTH_RE = re.compile(r"\b(\d{4})-(\d{2})\b")
_YEAR_RE = re.compile(r"\b(\d{4})\b")
_MDY_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

_MONTH_NAME = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t)?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
_MON_D_Y_RE = re.compile(rf"\b({_MONTH_NAME})\s+(\d{{1,2}}),\s*(\d{{4}})\b", re.IGNORECASE)
_D_MON_Y_RE = re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_NAME})\s+(\d{{4}})\b", re.IGNORECASE)
_MON_Y_RE = re.compile(rf"\b({_MONTH_NAME})\s+(\d{{4}})\b", re.IGNORECASE)

_ISO_RANGE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})\s+(?:to|through|-)\s+(\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)
_MON_DAY_RANGE_RE = re.compile(
    rf"\b({_MONTH_NAME})\s+(\d{{1,2}})\s*(?:-|–|—)\s*(\d{{1,2}}),\s*(\d{{4}})\b",
    re.IGNORECASE,
)

_REL_KEYWORD_RE = re.compile(r"^\s*(today|yesterday|tomorrow)\s*$", re.IGNORECASE)
_REL_IN_RE = re.compile(
    r"^\s*in\s+(\d+)\s+(day|days|week|weeks|month|months|year|years)\s*$",
    re.IGNORECASE,
)
_REL_AGO_RE = re.compile(
    r"^\s*(\d+)\s+(day|days|week|weeks|month|months|year|years)\s+ago\s*$",
    re.IGNORECASE,
)
_REL_WEEKDAY_RE = re.compile(
    r"^\s*(last|next|this)\s+(mon|monday|tue|tues|tuesday|wed|wednesday|thu|thur|thurs|thursday|"
    r"fri|friday|sat|saturday|sun|sunday)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DateRange:
    date_start: str
    date_end: str
    precision: str
    parser: str


@dataclass(frozen=True)
class RelativeSpec:
    kind: str
    value: Optional[int] = None
    unit: Optional[str] = None
    weekday: Optional[int] = None


def hash_redacted(value: str, length: int = 8) -> str:
    if not isinstance(value, str):
        return "00000000"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[: max(1, int(length))]


def parse_iso_datetime_to_date(value: str) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    return parsed.date().isoformat()


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_end(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    day = min(d.day, _month_end(year, month))
    return date(year, month, day)


def _add_years(d: date, years: int) -> date:
    year = d.year + years
    day = min(d.day, _month_end(year, d.month))
    return date(year, d.month, day)


def _month_number(value: str) -> Optional[int]:
    if not isinstance(value, str):
        return None
    return _MONTHS.get(value.strip().lower())


def _weekday_number(value: str) -> Optional[int]:
    if not isinstance(value, str):
        return None
    return _WEEKDAYS.get(value.strip().lower())


def _parse_iso_day(value: str) -> Optional[date]:
    match = _ISO_DAY_RE.fullmatch(value.strip())
    if not match:
        return None
    year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return _safe_date(year, month, day)


def _parse_mdy(value: str) -> Optional[date]:
    match = _MDY_RE.fullmatch(value.strip())
    if not match:
        return None
    month, day, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return _safe_date(year, month, day)


def _parse_month_name_day_year(month_text: str, day_text: str, year_text: str) -> Optional[date]:
    month = _month_number(month_text)
    if month is None:
        return None
    return _safe_date(int(year_text), month, int(day_text))


def parse_absolute_date_raw(date_raw: str) -> Tuple[Optional[DateRange], Optional[str]]:
    if not isinstance(date_raw, str) or not date_raw.strip():
        return None, "empty"
    text = date_raw.strip()

    iso_range = _ISO_RANGE_RE.search(text)
    if iso_range:
        left = _parse_iso_day(iso_range.group(1))
        right = _parse_iso_day(iso_range.group(2))
        if left and right:
            start = min(left, right).isoformat()
            end = max(left, right).isoformat()
            return DateRange(start, end, "range", "absolute_v1"), None
        return None, "invalid_format"

    mon_range = _MON_DAY_RANGE_RE.search(text)
    if mon_range:
        month_text, day_start_text, day_end_text, year_text = mon_range.groups()
        month = _month_number(month_text)
        if month is None:
            return None, "invalid_format"
        year = int(year_text)
        day_start = int(day_start_text)
        day_end = int(day_end_text)
        if day_end < day_start:
            return None, "invalid_format"
        left = _safe_date(year, month, day_start)
        right = _safe_date(year, month, day_end)
        if left and right:
            return DateRange(left.isoformat(), right.isoformat(), "range", "absolute_v1"), None
        return None, "invalid_format"

    candidates: list[tuple[int, int, DateRange]] = []

    for match in _ISO_DAY_RE.finditer(text):
        parsed = _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if parsed:
            iso = parsed.isoformat()
            candidates.append((match.start(), match.end(), DateRange(iso, iso, "day", "absolute_v1")))

    for match in _MDY_RE.finditer(text):
        parsed = _safe_date(int(match.group(3)), int(match.group(1)), int(match.group(2)))
        if parsed:
            iso = parsed.isoformat()
            candidates.append((match.start(), match.end(), DateRange(iso, iso, "day", "absolute_v1")))

    for match in _MON_D_Y_RE.finditer(text):
        parsed = _parse_month_name_day_year(match.group(1), match.group(2), match.group(3))
        if parsed:
            iso = parsed.isoformat()
            candidates.append((match.start(), match.end(), DateRange(iso, iso, "day", "absolute_v1")))

    for match in _D_MON_Y_RE.finditer(text):
        parsed = _parse_month_name_day_year(match.group(2), match.group(1), match.group(3))
        if parsed:
            iso = parsed.isoformat()
            candidates.append((match.start(), match.end(), DateRange(iso, iso, "day", "absolute_v1")))

    for match in _MON_Y_RE.finditer(text):
        month = _month_number(match.group(1))
        year = int(match.group(2))
        if month:
            start = date(year, month, 1).isoformat()
            end = date(year, month, _month_end(year, month)).isoformat()
            candidates.append((match.start(), match.end(), DateRange(start, end, "month", "absolute_v1")))

    for match in _ISO_MONTH_RE.finditer(text):
        year, month = int(match.group(1)), int(match.group(2))
        parsed = _safe_date(year, month, 1)
        if parsed:
            start = parsed.isoformat()
            end = date(year, month, _month_end(year, month)).isoformat()
            candidates.append((match.start(), match.end(), DateRange(start, end, "month", "absolute_v1")))

    for match in _YEAR_RE.finditer(text):
        year = int(match.group(1))
        start = _safe_date(year, 1, 1)
        end = _safe_date(year, 12, 31)
        if start and end:
            candidates.append((match.start(), match.end(), DateRange(start.isoformat(), end.isoformat(), "year", "absolute_v1")))

    if not candidates:
        return None, None

    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    pruned: list[tuple[int, int, DateRange]] = []
    for start, end, parsed in candidates:
        if any(prev_start <= start and end <= prev_end for prev_start, prev_end, _ in pruned):
            continue
        pruned.append((start, end, parsed))

    unique_ranges = {(entry[2].date_start, entry[2].date_end, entry[2].precision) for entry in pruned}
    if len(unique_ranges) != 1:
        return None, "unresolved_ambiguous"

    return pruned[0][2], None


def parse_relative_spec(date_raw: str) -> Optional[RelativeSpec]:
    if not isinstance(date_raw, str) or not date_raw.strip():
        return None
    text = date_raw.strip()

    keyword = _REL_KEYWORD_RE.match(text)
    if keyword:
        value = keyword.group(1).lower()
        return RelativeSpec(kind="keyword", unit=value)

    in_match = _REL_IN_RE.match(text)
    if in_match:
        amount = int(in_match.group(1))
        unit = in_match.group(2).lower()
        return RelativeSpec(kind="delta", value=amount, unit=unit)

    ago_match = _REL_AGO_RE.match(text)
    if ago_match:
        amount = int(ago_match.group(1))
        unit = ago_match.group(2).lower()
        return RelativeSpec(kind="delta", value=-amount, unit=unit)

    weekday_match = _REL_WEEKDAY_RE.match(text)
    if weekday_match:
        direction = weekday_match.group(1).lower()
        weekday = _weekday_number(weekday_match.group(2))
        if weekday is None:
            return None
        return RelativeSpec(kind="weekday", unit=direction, weekday=weekday)

    return None


def resolve_relative(spec: RelativeSpec, anchor_iso_date: str) -> Tuple[Optional[DateRange], Optional[str]]:
    if spec is None:
        return None, "unresolved_relative"
    if not isinstance(anchor_iso_date, str) or not anchor_iso_date.strip():
        return None, "missing_anchor"
    try:
        anchor = date.fromisoformat(anchor_iso_date.strip())
    except ValueError:
        return None, "missing_anchor"

    if spec.kind == "keyword":
        unit = (spec.unit or "").lower()
        if unit == "today":
            resolved = anchor
        elif unit == "yesterday":
            resolved = anchor - timedelta(days=1)
        elif unit == "tomorrow":
            resolved = anchor + timedelta(days=1)
        else:
            return None, "unresolved_relative"
        iso = resolved.isoformat()
        return DateRange(iso, iso, "day", "relative_v1"), None

    if spec.kind == "delta":
        if spec.value is None or not spec.unit:
            return None, "unresolved_relative"
        unit = spec.unit.lower()
        amount = int(spec.value)
        if unit in ("day", "days"):
            resolved = anchor + timedelta(days=amount)
        elif unit in ("week", "weeks"):
            resolved = anchor + timedelta(days=amount * 7)
        elif unit in ("month", "months"):
            resolved = _add_months(anchor, amount)
        elif unit in ("year", "years"):
            resolved = _add_years(anchor, amount)
        else:
            return None, "unresolved_relative"
        iso = resolved.isoformat()
        return DateRange(iso, iso, "day", "relative_v1"), None

    if spec.kind == "weekday":
        if spec.weekday is None or not spec.unit:
            return None, "unresolved_relative"
        direction = spec.unit.lower()
        target = int(spec.weekday)
        current = anchor.weekday()

        if direction == "last":
            delta_days = (current - target) % 7
            if delta_days == 0:
                delta_days = 7
            resolved = anchor - timedelta(days=delta_days)
        elif direction == "next":
            delta_days = (target - current) % 7
            if delta_days == 0:
                delta_days = 7
            resolved = anchor + timedelta(days=delta_days)
        elif direction == "this":
            delta_days = (target - current) % 7
            resolved = anchor + timedelta(days=delta_days)
        else:
            return None, "unresolved_relative"

        iso = resolved.isoformat()
        return DateRange(iso, iso, "day", "relative_v1"), None

    return None, "unresolved_relative"


def find_first_absolute_anchor(text: str, max_chars: int = 12000) -> Optional[str]:
    if not isinstance(text, str) or not text:
        return None
    sample = text[: max(0, int(max_chars))]

    candidates: list[tuple[int, str]] = []
    for match in _ISO_RANGE_RE.finditer(sample):
        candidates.append((match.start(), match.group(0)))
    for match in _MON_DAY_RANGE_RE.finditer(sample):
        candidates.append((match.start(), match.group(0)))
    for regex in (_ISO_DAY_RE, _MDY_RE, _MON_D_Y_RE, _D_MON_Y_RE, _MON_Y_RE, _ISO_MONTH_RE, _YEAR_RE):
        for match in regex.finditer(sample):
            candidates.append((match.start(), match.group(0)))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])

    for _, candidate_text in candidates:
        parsed, status = parse_absolute_date_raw(candidate_text)
        if status is None and parsed is not None:
            return parsed.date_start
    return None

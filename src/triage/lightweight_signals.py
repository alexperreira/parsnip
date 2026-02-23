import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Pattern, Sequence, Tuple


MAX_ANALYZE_CHARS = 50_000
MAX_TOKENS_FOR_SHAPES = 2_000
MAX_SHAPE_LEN = 40

_WORD_CHAR_RE = re.compile(r"[A-Za-z0-9_]")

DIALOGUE_MARKER_RE = re.compile(
    r"(\bQ:\s|\bA:\s|\bName:\s|\b[A-Z][a-z]{1,20}:\s|\u2014|\"[^\"]{1,200}\")",
    re.IGNORECASE,
)

DATE_LIKE_RE = re.compile(
    r"(\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|"
    r"\b(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|"
    r"Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)"
    r"\s+\d{1,2},\s+\d{4}\b)",
    re.IGNORECASE,
)

TIME_LIKE_RE = re.compile(
    r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|a\.m\.|p\.m\.)?\b",
    re.IGNORECASE,
)

TIME_OF_DAY_WORD_RE = re.compile(r"\b(morning|afternoon|evening|night)\b", re.IGNORECASE)

HONORIFIC_RE = re.compile(
    r"\b(Mr|Mrs|Ms|Dr|Prof|Officer|Det|Detective|Sgt|Sergeant|Lt|Lieutenant)\.?\b",
    re.IGNORECASE,
)

CAPITALIZED_NAME_RE = re.compile(
    r"\b[A-Z][a-z]{1,20}\s+(?:[A-Z]\.\s+)?[A-Z][a-z]{1,20}\b"
)

INITIALS_RE = re.compile(r"\b[A-Z]\.\s*[A-Z]\.\b")

ORG_SUFFIX_RE = re.compile(
    r"\b(Inc|LLC|Ltd|Corp|Co|Company|University|Dept|Department|Agency|Bureau|Commission)\.?\b",
    re.IGNORECASE,
)

BADGE_ID_RE = re.compile(
    r"\b(?:ID|Case|Report|Badge|Employee|Officer)\s*#?:?\s*[A-Z0-9][A-Z0-9-]{2,}\b",
    re.IGNORECASE,
)

INCIDENT_VERB_RE = re.compile(
    r"\b("
    r"arrest|interview|report|observe|respond|arriv|depart|transport|contact|call|meet|"
    r"search|seiz|testif|confess|assault|threaten|shoot|stab|rob|burglar|kidnap|"
    r"detain|pursu|follow|witness"
    r")\w*\b",
    re.IGNORECASE,
)

BULLET_LINE_RE = re.compile(r"^\s*(?:[-*•]|(?:\d{1,3}[.)]))\s+\S")


def _safe_text(text: Any) -> str:
    if text is None:
        return ""
    if isinstance(text, str):
        return text
    return str(text)


def _truncate_for_analysis(text: str) -> str:
    if "\x00" in text:
        text = text.replace("\x00", "")
    if len(text) <= MAX_ANALYZE_CHARS:
        return text
    return text[:MAX_ANALYZE_CHARS]


def _is_punctuation(ch: str) -> bool:
    if not ch or ch.isspace():
        return False
    return unicodedata.category(ch).startswith("P")


def _max_repeated_run(text: str) -> int:
    max_run = 0
    current_run = 0
    current_char = None
    for ch in text:
        if ch.isspace():
            continue
        if ch == current_char:
            current_run += 1
        else:
            current_char = ch
            current_run = 1
        if current_run > max_run:
            max_run = current_run
    return max_run


def _word_shape(token: str) -> str:
    shape_chars: List[str] = []
    for ch in token:
        if ch.isupper():
            shape_chars.append("A")
        elif ch.islower():
            shape_chars.append("a")
        elif ch.isdigit():
            shape_chars.append("0")
        elif ch == "_":
            shape_chars.append("_")
        else:
            shape_chars.append("-")
        if len(shape_chars) >= MAX_SHAPE_LEN:
            break
    return "".join(shape_chars)


def _iter_tokens(text: str) -> Iterable[str]:
    for tok in re.findall(r"\S+", text):
        yield tok


def _tableish_line(line: str) -> bool:
    if "\t" in line:
        return True
    if line.count("|") >= 2:
        return True
    return re.search(r"\S\s{2,}\S", line) is not None


def _keyword_boundaries(keyword: str) -> Tuple[str, str]:
    starts_word = bool(keyword) and _WORD_CHAR_RE.match(keyword[0]) is not None
    ends_word = bool(keyword) and _WORD_CHAR_RE.match(keyword[-1]) is not None
    left = r"(?<![A-Za-z0-9_])" if starts_word else ""
    right = r"(?![A-Za-z0-9_])" if ends_word else ""
    return left, right


def compile_keyword_packs(
    packs: Mapping[str, Sequence[str]],
) -> Dict[str, List[Pattern[str]]]:
    compiled: Dict[str, List[Pattern[str]]] = {}
    for pack_name, keywords in packs.items():
        if not pack_name or not isinstance(keywords, (list, tuple)):
            continue
        patterns: List[Pattern[str]] = []
        for raw_keyword in keywords:
            kw = _safe_text(raw_keyword).strip()
            if not kw:
                continue
            left, right = _keyword_boundaries(kw)
            patterns.append(re.compile(left + re.escape(kw) + right, re.IGNORECASE))
        if patterns:
            compiled[pack_name] = patterns
    return compiled


@dataclass(frozen=True)
class KeywordHitSummary:
    total: int
    by_pack: Dict[str, int]


def _keyword_hits(text: str, compiled_packs: Mapping[str, Sequence[Pattern[str]]]) -> KeywordHitSummary:
    if not compiled_packs:
        return KeywordHitSummary(total=0, by_pack={})
    total = 0
    by_pack: Dict[str, int] = {}
    for pack_name, patterns in compiled_packs.items():
        if not patterns:
            continue
        pack_total = 0
        for pattern in patterns:
            pack_total += len(list(pattern.finditer(text)))
        if pack_total:
            by_pack[pack_name] = pack_total
            total += pack_total
    return KeywordHitSummary(total=total, by_pack=by_pack)


def compute_lightweight_signals(
    text: Any,
    *,
    compiled_keyword_packs: Optional[Mapping[str, Sequence[Pattern[str]]]] = None,
) -> Dict[str, Any]:
    text = _truncate_for_analysis(_safe_text(text))
    total_chars = len(text)
    non_ws_chars = sum(1 for ch in text if not ch.isspace())
    non_ws_ratio = (non_ws_chars / total_chars) if total_chars else 0.0

    punctuation_chars = sum(1 for ch in text if _is_punctuation(ch))
    punctuation_ratio = (punctuation_chars / non_ws_chars) if non_ws_chars else 0.0

    replacement_char_count = text.count("\uFFFD")
    max_repeat_run = _max_repeated_run(text)

    tokens_seen = 0
    shapes: set = set()
    for tok in _iter_tokens(text):
        if tokens_seen >= MAX_TOKENS_FOR_SHAPES:
            break
        shapes.add(_word_shape(tok))
        tokens_seen += 1
    word_shape_diversity = (len(shapes) / tokens_seen) if tokens_seen else 0.0

    lines = text.splitlines()
    nonempty_lines = [ln for ln in lines if ln.strip()]
    bullet_lines = sum(1 for ln in nonempty_lines if BULLET_LINE_RE.search(ln) is not None)
    bullet_density = (bullet_lines / len(nonempty_lines)) if nonempty_lines else 0.0

    tableish_lines = sum(1 for ln in nonempty_lines if _tableish_line(ln))
    table_like = tableish_lines >= 2

    dialogue_markers = len(list(DIALOGUE_MARKER_RE.finditer(text)))
    timestamp_hits = len(list(TIME_LIKE_RE.finditer(text)))
    time_of_day_word_hits = len(list(TIME_OF_DAY_WORD_RE.finditer(text)))

    honorific_hits = len(list(HONORIFIC_RE.finditer(text)))
    name_like_hits = len(list(CAPITALIZED_NAME_RE.finditer(text)))
    initials_hits = len(list(INITIALS_RE.finditer(text)))
    org_suffix_hits = len(list(ORG_SUFFIX_RE.finditer(text)))
    badge_id_hits = len(list(BADGE_ID_RE.finditer(text)))

    date_like_hits = len(list(DATE_LIKE_RE.finditer(text)))
    incident_verb_hits = len(list(INCIDENT_VERB_RE.finditer(text)))

    on_at_by_date_hits = 0
    if date_like_hits:
        # Only run the more expensive proximity regex when we already see a date-like string.
        on_at_by_date_re = re.compile(
            r"\b(on|at|by)\b.{0,20}?" + DATE_LIKE_RE.pattern,
            re.IGNORECASE | re.DOTALL,
        )
        on_at_by_date_hits = len(list(on_at_by_date_re.finditer(text)))

    keyword_summary = _keyword_hits(text, compiled_keyword_packs or {})

    features: Dict[str, Any] = {
        "text_quality": {
            "char_len": total_chars,
            "non_ws_ratio": round(non_ws_ratio, 6),
            "punctuation_ratio": round(punctuation_ratio, 6),
            "replacement_char_count": replacement_char_count,
            "max_repeated_char_run": max_repeat_run,
            "word_shape_diversity": round(word_shape_diversity, 6),
            "unique_word_shapes": len(shapes),
            "tokens_sampled_for_shapes": tokens_seen,
        },
        "structure": {
            "line_count": len(lines),
            "nonempty_line_count": len(nonempty_lines),
            "bullet_line_count": bullet_lines,
            "bullet_density": round(bullet_density, 6),
            "tableish_line_count": tableish_lines,
            "table_like": table_like,
            "dialogue_marker_count": dialogue_markers,
            "timestamp_count": timestamp_hits,
        },
        "entity_hints": {
            "honorific_count": honorific_hits,
            "capitalized_name_count": name_like_hits,
            "initials_count": initials_hits,
            "org_suffix_count": org_suffix_hits,
            "badge_id_count": badge_id_hits,
        },
        "event_hints": {
            "date_like_count": date_like_hits,
            "time_like_count": timestamp_hits,
            "time_of_day_word_count": time_of_day_word_hits,
            "on_at_by_date_count": on_at_by_date_hits,
            "incident_verb_count": incident_verb_hits,
        },
    }

    if compiled_keyword_packs is not None:
        features["domain_keywords"] = {
            "keyword_hit_total": keyword_summary.total,
            "keyword_hit_by_pack": keyword_summary.by_pack,
        }

    return features


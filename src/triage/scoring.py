from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def estimate_tokens(text: Any) -> int:
    """
    Rough token estimate used for deterministic budgeting.

    This intentionally avoids external tokenizers. It only needs to be stable and monotonic enough
    to cap work, not accurate.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if not text:
        return 0
    return max(1, len(text) // 4)


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def score_from_features(features: Mapping[str, Any]) -> float:
    """
    Deterministic scoring model (v1).

    Input is the dict returned by `triage.lightweight_signals.compute_lightweight_signals()`.
    """
    tq = features.get("text_quality") or {}
    struct = features.get("structure") or {}
    entity = features.get("entity_hints") or {}
    event = features.get("event_hints") or {}
    domain = features.get("domain_keywords") or {}
    ner = features.get("ner") or {}

    score = 0.0

    # Domain keywords are the strongest generic signal.
    kw_total = _as_int(domain.get("keyword_hit_total")) or 0
    score += min(0.4, 0.05 * kw_total)

    # Entity-ish hints.
    if (_as_int(entity.get("badge_id_count")) or 0) > 0:
        score += 0.10
    if (_as_int(entity.get("capitalized_name_count")) or 0) > 0:
        score += 0.08
    if (_as_int(entity.get("honorific_count")) or 0) > 0:
        score += 0.05
    if (_as_int(entity.get("org_suffix_count")) or 0) > 0:
        score += 0.05

    # Event-ish hints.
    if (_as_int(event.get("date_like_count")) or 0) > 0:
        score += 0.15
    if (_as_int(event.get("time_like_count")) or 0) > 0:
        score += 0.05
    if (_as_int(event.get("on_at_by_date_count")) or 0) > 0:
        score += 0.10
    if (_as_int(event.get("incident_verb_count")) or 0) > 0:
        score += 0.10

    # Structure hints.
    if (_as_int(struct.get("dialogue_marker_count")) or 0) > 0:
        score += 0.08
    if struct.get("table_like") is True:
        score += 0.03
    bullet_density = struct.get("bullet_density")
    if isinstance(bullet_density, (int, float)) and bullet_density >= 0.2:
        score += 0.03

    # Optional NER boosts.
    if ner.get("available") is True:
        counts = ner.get("counts_by_label") or {}
        if (_as_int(counts.get("PERSON")) or 0) > 0:
            score += 0.10
        if (_as_int(counts.get("ORG")) or 0) > 0:
            score += 0.05
        if (_as_int(counts.get("GPE")) or 0) > 0:
            score += 0.05
        if (_as_int(counts.get("DATE")) or 0) > 0:
            score += 0.08

    # Quality gates (penalties). Avoid hard skipping here; Stage 4 routing decides.
    char_len = _as_int(tq.get("char_len")) or 0
    non_ws_ratio = tq.get("non_ws_ratio")
    punctuation_ratio = tq.get("punctuation_ratio")
    max_repeat = _as_int(tq.get("max_repeated_char_run")) or 0

    if char_len < 40:
        score -= 0.20
    if isinstance(non_ws_ratio, (int, float)) and non_ws_ratio < 0.05:
        score -= 0.20
    if isinstance(punctuation_ratio, (int, float)) and punctuation_ratio > 0.40:
        score -= 0.10
    if max_repeat >= 10:
        score -= 0.10

    return _clamp01(score)


@dataclass(frozen=True)
class BudgetDecision:
    selected_chunk_ids: List[str]
    skipped_chunk_ids: List[str]
    selected_tokens_est: int
    selected_total: int
    skipped_total: int


def select_under_budgets(
    chunks: Sequence[Mapping[str, Any]],
    *,
    max_llm_chunks: Optional[int] = None,
    max_llm_chunks_per_file: Optional[int] = None,
    max_llm_tokens: Optional[int] = None,
    allow_file_ids: Optional[Iterable[str]] = None,
    deny_file_ids: Optional[Iterable[str]] = None,
) -> BudgetDecision:
    """
    Deterministically select chunk_ids under global/per-file/token budgets.

    Chunks are ranked by score desc, then chunk_id asc (stable tie-break).
    Expected chunk shape:
      - chunk_id (str)
      - file_id (str)
      - text (str-ish)
      - signals or features (dict): if `features` is absent, `signals` is accepted.
    """
    allow = set(allow_file_ids or [])
    deny = set(deny_file_ids or [])

    ranked: List[Tuple[float, str, str, int]] = []
    skipped: List[str] = []
    for chunk in chunks:
        chunk_id = chunk.get("chunk_id")
        file_id = chunk.get("file_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            continue
        if not isinstance(file_id, str) or not file_id:
            skipped.append(chunk_id)
            continue
        if allow and file_id not in allow:
            skipped.append(chunk_id)
            continue
        if deny and file_id in deny:
            skipped.append(chunk_id)
            continue
        features = chunk.get("features")
        if not isinstance(features, Mapping):
            features = chunk.get("signals") if isinstance(chunk.get("signals"), Mapping) else {}
        score = score_from_features(features)
        tok = estimate_tokens(chunk.get("text"))
        ranked.append((score, chunk_id, file_id, tok))

    ranked.sort(key=lambda item: (-item[0], item[1]))

    selected_ids: List[str] = []
    skipped_ids: List[str] = []
    per_file: Dict[str, int] = {}
    total_tokens = 0

    for score, chunk_id, file_id, tok in ranked:
        _ = score
        if max_llm_chunks is not None and len(selected_ids) >= max_llm_chunks:
            skipped_ids.append(chunk_id)
            continue
        if max_llm_chunks_per_file is not None and per_file.get(file_id, 0) >= max_llm_chunks_per_file:
            skipped_ids.append(chunk_id)
            continue
        if max_llm_tokens is not None and (total_tokens + tok) > max_llm_tokens:
            skipped_ids.append(chunk_id)
            continue
        selected_ids.append(chunk_id)
        per_file[file_id] = per_file.get(file_id, 0) + 1
        total_tokens += tok

    skipped_ids.extend(skipped)
    return BudgetDecision(
        selected_chunk_ids=selected_ids,
        skipped_chunk_ids=skipped_ids,
        selected_tokens_est=total_tokens,
        selected_total=len(selected_ids),
        skipped_total=len(skipped_ids),
    )


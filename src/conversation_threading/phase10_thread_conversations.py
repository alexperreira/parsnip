import argparse
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from file_parser.compress_io import open_text_reader
from loaders.store import connect_db, ensure_schema
from timeline.date_parse import find_first_absolute_anchor, parse_iso_datetime_to_date


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "her",
    "here",
    "him",
    "his",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "just",
    "me",
    "my",
    "no",
    "not",
    "of",
    "on",
    "or",
    "our",
    "out",
    "she",
    "so",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "to",
    "up",
    "us",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "who",
    "will",
    "with",
    "would",
    "you",
    "your",
}


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Thread conversations across documents in SQLite (Phase 10)."
    )
    parser.add_argument(
        "--db",
        default="output/store.sqlite",
        help="SQLite DB path (default: output/store.sqlite).",
    )
    parser.add_argument(
        "--chunks",
        default=None,
        help="Optional chunks.jsonl path (used only for chunk-level anchor dates).",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Optional manifest.jsonl path (used only for file-level anchor dates if files table is empty).",
    )
    parser.add_argument(
        "--reset",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Clear existing conversation threading tables before rebuilding (default: true).",
    )
    parser.add_argument(
        "--max-segments",
        type=int,
        default=None,
        help="Optional limit on segments processed (for debugging).",
    )
    parser.add_argument(
        "--max-key-fanout",
        type=int,
        default=200,
        help="Ignore candidate keys that match >N segments in a case (default: 200).",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=500,
        help="Cap evaluated candidates per segment (default: 500).",
    )
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=0,
        help="Print progress every N seconds (0 disables).",
    )
    return parser.parse_args()


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _normalize_text(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if not value:
        return None
    value = _NON_ALNUM_RE.sub(" ", value)
    value = " ".join(value.split())
    return value if value else None


def _tokenize(text: str, max_tokens: int = 200) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return []
    cleaned = _normalize_text(text)
    if not cleaned:
        return []
    tokens = []
    for token in cleaned.split():
        if len(tokens) >= max_tokens:
            break
        if token in _STOPWORDS:
            continue
        if token.isdigit():
            continue
        if len(token) < 3:
            continue
        tokens.append(token)
    return tokens


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_iso_date(value: str) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter <= 0:
        return 0.0
    union = len(a | b)
    return inter / union if union else 0.0


def _time_bonus(a: str | None, b: str | None) -> tuple[float, str | None]:
    left = _parse_iso_date(a) if a else None
    right = _parse_iso_date(b) if b else None
    if not left or not right:
        return 0.0, None
    delta = abs((left - right).days)
    if delta <= 7:
        return 0.1, "time_within_7d"
    if delta <= 30:
        return 0.05, "time_within_30d"
    return 0.0, None


def _maybe_print_progress(processed, started, last_print, interval, label):
    if interval <= 0:
        return last_print
    now = time.monotonic()
    if now - last_print < interval:
        return last_print
    elapsed = round(now - started, 3)
    print(f"Threading progress: {label}={processed} elapsed_seconds={elapsed}", flush=True)
    return now


def _lookup_case_id_norm(conn, file_id: str, chunk_id: str, has_identity_signals: bool, has_event_cases: bool):
    if has_identity_signals:
        row = conn.execute(
            "SELECT MIN(value_norm) FROM identity_signals "
            "WHERE attribute='case_id' AND value_norm IS NOT NULL AND file_id=? AND chunk_id=?",
            (file_id, chunk_id),
        ).fetchone()
        if row and row[0]:
            return str(row[0]), "identity_signals"
    if has_event_cases:
        row = conn.execute(
            "SELECT MIN(ec.case_id_norm) "
            "FROM events e JOIN event_cases ec ON e.event_id = ec.event_id "
            "WHERE e.file_id=? AND e.chunk_id=?",
            (file_id, chunk_id),
        ).fetchone()
        if row and row[0]:
            return str(row[0]), "event_cases"
    return f"file:{file_id}", "fallback_file"


def _load_person_display_map(conn) -> dict[str, int]:
    if not _table_exists(conn, "person_clusters"):
        return {}
    mapping: dict[str, int] = {}
    for person_id, display_norm in conn.execute(
        "SELECT person_id, display_name_norm FROM person_clusters"
    ):
        if person_id is None or not isinstance(display_norm, str) or not display_norm.strip():
            continue
        key = display_norm.strip().lower()
        existing = mapping.get(key)
        if existing is None or int(person_id) < existing:
            mapping[key] = int(person_id)
    return mapping


def _file_anchor_date(conn, file_id: str, cache: dict[str, str | None]) -> str | None:
    if file_id in cache:
        return cache[file_id]
    if not _table_exists(conn, "files"):
        cache[file_id] = None
        return None
    row = conn.execute("SELECT mtime_utc FROM files WHERE file_id=?", (file_id,)).fetchone()
    anchor = parse_iso_datetime_to_date(row[0]) if row and row[0] else None
    cache[file_id] = anchor
    return anchor


def _update_anchor_dates_from_chunks(conn, chunks_path: Path):
    updated = 0
    total = 0
    with open_text_reader(chunks_path) as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            total += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            file_id = record.get("file_id")
            chunk_id = record.get("chunk_id")
            text = record.get("text") or ""
            if not isinstance(file_id, str) or not isinstance(chunk_id, str):
                continue
            anchor = find_first_absolute_anchor(text)
            if not anchor:
                continue
            result = conn.execute(
                "UPDATE conversation_segments SET anchor_date=? "
                "WHERE file_id=? AND chunk_id=? AND (anchor_date IS NULL OR anchor_date='')",
                (anchor, file_id, chunk_id),
            )
            if result.rowcount:
                updated += int(result.rowcount)
    conn.commit()
    return {"chunks_records_total": total, "chunk_anchor_updates": updated}


def _update_anchor_dates_from_manifest(conn, manifest_path: Path):
    updated = 0
    total = 0
    with open_text_reader(manifest_path) as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            total += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            file_id = record.get("file_id")
            if not isinstance(file_id, str):
                continue
            anchor = parse_iso_datetime_to_date(record.get("mtime"))
            if not anchor:
                continue
            result = conn.execute(
                "UPDATE conversation_segments SET anchor_date=? "
                "WHERE file_id=? AND (anchor_date IS NULL OR anchor_date='')",
                (anchor, file_id),
            )
            if result.rowcount:
                updated += int(result.rowcount)
    conn.commit()
    return {"manifest_records_total": total, "manifest_anchor_updates": updated}


@dataclass(frozen=True)
class _Segment:
    segment_id: int
    file_id: str
    chunk_id: str
    anchor_date: str | None
    participant_keys: set[str]
    participants: list[dict]
    topic_tokens: set[str]
    top_tokens: list[str]


class _UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}
        self.rank = {i: 0 for i in items}

    def find(self, x):
        parent = self.parent
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(self, a, b):
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        rank = self.rank
        if rank[ra] < rank[rb]:
            self.parent[ra] = rb
        elif rank[ra] > rank[rb]:
            self.parent[rb] = ra
        else:
            self.parent[rb] = ra
            rank[ra] += 1


def _label_thread(segments: list[_Segment]) -> str | None:
    token_counts = Counter()
    for seg in segments:
        token_counts.update(seg.top_tokens)
    top_tokens = [t for t, _ in sorted(token_counts.items(), key=lambda kv: (-kv[1], kv[0]))][:3]
    if top_tokens:
        return "Conversation: " + ", ".join(top_tokens)

    part_counts = Counter()
    for seg in segments:
        for pk in seg.participant_keys:
            part_counts[pk] += 1
    if part_counts:
        pk, _ = sorted(part_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        if pk.startswith("p:"):
            return f"Conversation: participant {pk[2:]}"
        if pk.startswith("s:"):
            return "Conversation: " + pk[2:][:40]
    return None


def build_thread_conversations(
    db_path,
    chunks_path=None,
    manifest_path=None,
    reset=True,
    max_segments=None,
    max_key_fanout=200,
    max_candidates=500,
    progress_interval=0,
):
    started = time.monotonic()
    conn = connect_db(db_path)
    ensure_schema(conn, overwrite=False)

    has_identity_signals = _table_exists(conn, "identity_signals")
    has_event_cases = _table_exists(conn, "event_cases") and _table_exists(conn, "events")

    person_display_map = _load_person_display_map(conn)
    file_anchor_cache: dict[str, str | None] = {}

    summary = {
        "utterances_total": 0,
        "segments_built": 0,
        "segments_with_person_ids": 0,
        "segments_with_anchor_date_initial": 0,
        "segments_with_case_from_identity_signals": 0,
        "segments_with_case_from_event_cases": 0,
        "segments_with_case_fallback_file": 0,
        "thread_edges_link": 0,
        "thread_edges_needs_review": 0,
        "threads_created": 0,
        "thread_memberships": 0,
        "thread_participants": 0,
    }

    cursor = conn.execute(
        "SELECT file_id, chunk_id, page_start, page_end, speaker, quote "
        "FROM conversations ORDER BY file_id, chunk_id, conversation_id"
    )

    def flush_segment(state):
        if not state:
            return
        file_id = state["file_id"]
        chunk_id = state["chunk_id"]
        if not isinstance(file_id, str) or not isinstance(chunk_id, str):
            return
        utterance_count = int(state["utterance_count"])
        if utterance_count <= 0:
            return
        case_id_norm, case_source = _lookup_case_id_norm(
            conn, file_id, chunk_id, has_identity_signals, has_event_cases
        )
        if case_source == "identity_signals":
            summary["segments_with_case_from_identity_signals"] += 1
        elif case_source == "event_cases":
            summary["segments_with_case_from_event_cases"] += 1
        else:
            summary["segments_with_case_fallback_file"] += 1

        anchor_date = _file_anchor_date(conn, file_id, file_anchor_cache)
        if anchor_date:
            summary["segments_with_anchor_date_initial"] += 1

        speakers = sorted(state["speakers"])
        participants = []
        participant_keys = set()
        has_person_id = False
        for speaker_norm in speakers:
            person_id = person_display_map.get(speaker_norm)
            if person_id is not None:
                has_person_id = True
                participant_key = f"p:{person_id}"
                participants.append(
                    {
                        "participant_key": participant_key,
                        "person_id": person_id,
                        "speaker_norm": speaker_norm,
                        "source": "person_clusters",
                    }
                )
            else:
                participant_key = f"s:{speaker_norm}"
                participants.append(
                    {
                        "participant_key": participant_key,
                        "person_id": None,
                        "speaker_norm": speaker_norm,
                        "source": "speaker_norm",
                    }
                )
            participant_keys.add(participant_key)

        if has_person_id:
            summary["segments_with_person_ids"] += 1

        token_counts: Counter[str] = state["token_counts"]
        top_tokens = [
            token
            for token, _ in sorted(token_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            if token
        ][:20]
        topic_signature = _sha256_hex(" ".join(top_tokens))[:16]

        participants_json = json.dumps(participants, ensure_ascii=True, separators=(",", ":"))
        features_json = json.dumps(
            {"top_tokens": top_tokens, "topic_signature": topic_signature},
            ensure_ascii=True,
            separators=(",", ":"),
        )

        conn.execute(
            "INSERT OR IGNORE INTO conversation_segments("
            "file_id, chunk_id, page_start, page_end, case_id_norm, case_source, anchor_date, "
            "utterance_count, participants_json, features_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                file_id,
                chunk_id,
                state["page_start"],
                state["page_end"],
                case_id_norm,
                case_source,
                anchor_date,
                utterance_count,
                participants_json,
                features_json,
            ),
        )
        conn.execute(
            "UPDATE conversation_segments SET "
            "page_start=?, page_end=?, case_id_norm=?, case_source=?, anchor_date=?, utterance_count=?, "
            "participants_json=?, features_json=? "
            "WHERE file_id=? AND chunk_id=?",
            (
                state["page_start"],
                state["page_end"],
                case_id_norm,
                case_source,
                anchor_date,
                utterance_count,
                participants_json,
                features_json,
                file_id,
                chunk_id,
            ),
        )
        summary["segments_built"] += 1

    last_key = None
    state = None
    for file_id, chunk_id, page_start, page_end, speaker, quote in cursor:
        if max_segments is not None and summary["segments_built"] >= max_segments:
            break
        if not isinstance(file_id, str) or not isinstance(chunk_id, str):
            continue
        key = (file_id, chunk_id)
        if last_key is None:
            last_key = key
            state = {
                "file_id": file_id,
                "chunk_id": chunk_id,
                "page_start": page_start,
                "page_end": page_end,
                "utterance_count": 0,
                "speakers": set(),
                "token_counts": Counter(),
            }
        elif key != last_key:
            flush_segment(state)
            last_key = key
            state = {
                "file_id": file_id,
                "chunk_id": chunk_id,
                "page_start": page_start,
                "page_end": page_end,
                "utterance_count": 0,
                "speakers": set(),
                "token_counts": Counter(),
            }

        summary["utterances_total"] += 1
        if page_start is not None:
            if state["page_start"] is None or int(page_start) < int(state["page_start"]):
                state["page_start"] = int(page_start)
        if page_end is not None:
            if state["page_end"] is None or int(page_end) > int(state["page_end"]):
                state["page_end"] = int(page_end)
        speaker_norm = _normalize_text(speaker) if isinstance(speaker, str) else None
        if speaker_norm:
            state["speakers"].add(speaker_norm)
        for token in _tokenize(quote or ""):
            state["token_counts"][token] += 1
        state["utterance_count"] += 1

    flush_segment(state)
    conn.commit()

    anchors_summary = {}
    if chunks_path:
        anchors_summary.update(_update_anchor_dates_from_chunks(conn, Path(chunks_path)))
    if manifest_path:
        # Only used if `files` table is not populated for a file.
        anchors_summary.update(_update_anchor_dates_from_manifest(conn, Path(manifest_path)))

    if reset:
        conn.execute("DELETE FROM conversation_thread_participants")
        conn.execute("DELETE FROM conversation_thread_segments")
        conn.execute("DELETE FROM conversation_threads")
        conn.execute("DELETE FROM conversation_thread_edges")
        conn.commit()

    # Thread per case_id_norm to avoid accidental cross-case blending.
    case_cursor = conn.execute(
        "SELECT case_id_norm, segment_id, file_id, chunk_id, anchor_date, participants_json, features_json "
        "FROM conversation_segments ORDER BY case_id_norm, segment_id"
    )

    last_case = None
    case_segments: list[_Segment] = []

    def flush_case(case_id_norm: str, segments: list[_Segment]):
        if not case_id_norm or not segments:
            return
        seg_ids = [s.segment_id for s in segments]
        uf = _UnionFind(seg_ids)

        inverted: dict[str, list[int]] = defaultdict(list)
        for seg in segments:
            for key in seg.participant_keys:
                inverted[f"k:{key}"].append(seg.segment_id)
            for tok in seg.topic_tokens:
                inverted[f"t:{tok}"].append(seg.segment_id)

        allowed_keys = {k for k, v in inverted.items() if len(v) <= int(max_key_fanout)}

        seg_by_id = {seg.segment_id: seg for seg in segments}
        seg_keys = {}
        for seg in segments:
            keys = []
            for pk in seg.participant_keys:
                keys.append(f"k:{pk}")
            for tok in seg.topic_tokens:
                keys.append(f"t:{tok}")
            seg_keys[seg.segment_id] = keys

        for seg in segments:
            counts = Counter()
            for key in seg_keys[seg.segment_id]:
                if key not in allowed_keys:
                    continue
                for other_id in inverted.get(key, []):
                    if other_id == seg.segment_id:
                        continue
                    left, right = (seg.segment_id, other_id)
                    if left > right:
                        left, right = right, left
                    counts[(left, right)] += 1
            if not counts:
                continue
            pairs = [
                pair
                for pair, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
            ][: int(max_candidates)]

            for left_id, right_id in pairs:
                left_seg = seg_by_id[left_id]
                right_seg = seg_by_id[right_id]

                participant_j = _jaccard(left_seg.participant_keys, right_seg.participant_keys)
                topic_j = _jaccard(left_seg.topic_tokens, right_seg.topic_tokens)
                bonus, bonus_reason = _time_bonus(left_seg.anchor_date, right_seg.anchor_date)
                score = 0.7 * participant_j + 0.3 * topic_j + bonus

                reasons = []
                if participant_j > 0:
                    reasons.append("participant_overlap")
                if topic_j > 0:
                    reasons.append("topic_overlap")
                if bonus_reason:
                    reasons.append(bonus_reason)
                reasons = sorted(set(reasons))

                decision = None
                if participant_j > 0 and score >= 0.45:
                    decision = "link"
                    uf.union(left_id, right_id)
                    summary["thread_edges_link"] += 1
                elif participant_j == 0 and topic_j >= 0.6 and score >= 0.3:
                    decision = "needs_review"
                    summary["thread_edges_needs_review"] += 1
                else:
                    continue

                conn.execute(
                    "INSERT OR REPLACE INTO conversation_thread_edges("
                    "left_segment_id, right_segment_id, score, decision, reasons_json"
                    ") VALUES (?, ?, ?, ?, ?)",
                    (
                        left_id,
                        right_id,
                        float(round(score, 6)),
                        decision,
                        json.dumps(reasons, ensure_ascii=True, separators=(",", ":")),
                    ),
                )

        clusters: dict[int, list[_Segment]] = defaultdict(list)
        for seg in segments:
            clusters[uf.find(seg.segment_id)].append(seg)

        for cluster_segments in clusters.values():
            cluster_segments = sorted(cluster_segments, key=lambda s: (s.anchor_date or "", s.file_id, s.chunk_id))
            segment_fps = sorted([f"{s.file_id}:{s.chunk_id}" for s in cluster_segments])
            thread_key = _sha256_hex(case_id_norm + "|" + ",".join(segment_fps))
            label = _label_thread(cluster_segments)

            conn.execute(
                "INSERT OR IGNORE INTO conversation_threads("
                "case_id_norm, thread_key, label, label_method, created_utc"
                ") VALUES (?, ?, ?, ?, datetime('now'))",
                (case_id_norm, thread_key, label, "keywords_v1"),
            )
            row = conn.execute(
                "SELECT thread_id FROM conversation_threads WHERE case_id_norm=? AND thread_key=?",
                (case_id_norm, thread_key),
            ).fetchone()
            if not row:
                continue
            thread_id = int(row[0])
            summary["threads_created"] += 1

            for seg in cluster_segments:
                sort_date = seg.anchor_date or "9999-12-31"
                sort_key = f"{sort_date}|{seg.file_id}|{seg.chunk_id}"
                conn.execute(
                    "INSERT OR IGNORE INTO conversation_thread_segments(thread_id, segment_id, sort_key) "
                    "VALUES (?, ?, ?)",
                    (thread_id, seg.segment_id, sort_key),
                )
                summary["thread_memberships"] += 1

            participant_rows = {}
            for seg in cluster_segments:
                for p in seg.participants:
                    pk = p.get("participant_key")
                    if not isinstance(pk, str) or not pk:
                        continue
                    participant_rows[pk] = (
                        thread_id,
                        pk,
                        p.get("person_id"),
                        p.get("speaker_norm"),
                        p.get("source") or "speaker_norm",
                    )
            for row in sorted(participant_rows.values(), key=lambda r: str(r[1])):
                conn.execute(
                    "INSERT OR IGNORE INTO conversation_thread_participants("
                    "thread_id, participant_key, person_id, speaker_norm, source"
                    ") VALUES (?, ?, ?, ?, ?)",
                    row,
                )
                summary["thread_participants"] += 1

    progress_started = time.monotonic()
    last_progress = progress_started

    for case_id_norm, segment_id, file_id, chunk_id, anchor_date, participants_json, features_json in case_cursor:
        if not isinstance(case_id_norm, str) or not case_id_norm:
            continue
        if last_case is None:
            last_case = case_id_norm
        if case_id_norm != last_case:
            flush_case(last_case, case_segments)
            case_segments = []
            last_case = case_id_norm
            last_progress = _maybe_print_progress(
                summary["threads_created"], progress_started, last_progress, progress_interval, "threads"
            )

        try:
            participants = json.loads(participants_json) if isinstance(participants_json, str) else []
        except json.JSONDecodeError:
            participants = []
        try:
            features = json.loads(features_json) if isinstance(features_json, str) else {}
        except json.JSONDecodeError:
            features = {}

        participant_keys = set()
        cleaned_participants = []
        if isinstance(participants, list):
            for entry in participants:
                if not isinstance(entry, dict):
                    continue
                pk = entry.get("participant_key")
                if not isinstance(pk, str) or not pk:
                    continue
                participant_keys.add(pk)
                cleaned_participants.append(entry)

        top_tokens = []
        if isinstance(features, dict):
            tt = features.get("top_tokens")
            if isinstance(tt, list):
                for token in tt:
                    if isinstance(token, str) and token:
                        top_tokens.append(token)
        topic_tokens = set(top_tokens)

        case_segments.append(
            _Segment(
                segment_id=int(segment_id),
                file_id=str(file_id),
                chunk_id=str(chunk_id),
                anchor_date=str(anchor_date) if isinstance(anchor_date, str) and anchor_date else None,
                participant_keys=participant_keys,
                participants=cleaned_participants,
                topic_tokens=topic_tokens,
                top_tokens=top_tokens,
            )
        )

    if last_case is not None:
        flush_case(last_case, case_segments)

    conn.commit()
    conn.close()

    elapsed = round(time.monotonic() - started, 3)
    return {
        "summary": summary,
        "anchors": anchors_summary,
        "elapsed_seconds": elapsed,
        "config": {
            "reset": bool(reset),
            "max_segments": max_segments,
            "max_key_fanout": int(max_key_fanout),
            "max_candidates": int(max_candidates),
        },
    }


def main():
    args = _parse_args()
    result = build_thread_conversations(
        db_path=args.db,
        chunks_path=args.chunks,
        manifest_path=args.manifest,
        reset=args.reset,
        max_segments=args.max_segments,
        max_key_fanout=args.max_key_fanout,
        max_candidates=args.max_candidates,
        progress_interval=args.progress_interval,
    )

    print("Phase 10 conversation threading summary")
    s = result["summary"]
    print(f"  utterances_total: {s['utterances_total']}")
    print(f"  segments_built: {s['segments_built']}")
    print(f"  segments_with_person_ids: {s['segments_with_person_ids']}")
    print(f"  segments_with_anchor_date_initial: {s['segments_with_anchor_date_initial']}")
    print(f"  case_links_identity_signals: {s['segments_with_case_from_identity_signals']}")
    print(f"  case_links_event_cases: {s['segments_with_case_from_event_cases']}")
    print(f"  case_links_fallback_file: {s['segments_with_case_fallback_file']}")
    print(f"  thread_edges_link: {s['thread_edges_link']}")
    print(f"  thread_edges_needs_review: {s['thread_edges_needs_review']}")
    print(f"  threads_created: {s['threads_created']}")
    print(f"  thread_memberships: {s['thread_memberships']}")
    print(f"  thread_participants: {s['thread_participants']}")
    if result["anchors"]:
        a = result["anchors"]
        if "chunk_anchor_updates" in a:
            print(f"  chunk_anchor_updates: {a['chunk_anchor_updates']}")
        if "manifest_anchor_updates" in a:
            print(f"  manifest_anchor_updates: {a['manifest_anchor_updates']}")
    print(f"  elapsed_seconds: {result['elapsed_seconds']}")


if __name__ == "__main__":
    main()

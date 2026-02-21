from dataclasses import dataclass
from typing import Optional

from loaders.store import connect_db

from file_parser.ui_shell import WidgetState, build_widget_error


@dataclass(frozen=True)
class TimelineProvenance:
    source_table: str
    record_id: int
    file_id: str
    chunk_id: str
    page_start: Optional[int]
    page_end: Optional[int]


@dataclass(frozen=True)
class TimelineEventRow:
    event_id: int
    event: str
    date_raw: Optional[str]
    date_start: Optional[str]
    date_end: Optional[str]
    precision: Optional[str]
    status: str
    parser: Optional[str]
    anchor_date: Optional[str]
    confidence: Optional[float]
    quote: Optional[str]
    provenance: TimelineProvenance


@dataclass(frozen=True)
class TimelineResult:
    status: int
    code: str
    case_id_norm: str
    normalized_rows: list[TimelineEventRow]
    unresolved_rows: list[TimelineEventRow]
    widget_states: list[WidgetState]


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _load_rows(conn, case_id_norm: str, limit: int) -> list[tuple]:
    has_event_times = _table_exists(conn, "event_times")
    if has_event_times:
        return conn.execute(
            "SELECT e.event_id, e.event, e.date, et.date_start, et.date_end, et.precision, "
            "COALESCE(et.status, 'missing') AS status, et.parser, et.anchor_date, "
            "e.confidence, e.quote, e.file_id, e.chunk_id, e.page_start, e.page_end "
            "FROM events e "
            "JOIN event_cases ec ON ec.event_id=e.event_id "
            "LEFT JOIN event_times et ON et.event_id=e.event_id "
            "WHERE ec.case_id_norm=? "
            "ORDER BY e.event_id ASC "
            "LIMIT ?",
            (case_id_norm, int(limit)),
        ).fetchall()
    return conn.execute(
        "SELECT e.event_id, e.event, e.date, NULL, NULL, NULL, 'missing', NULL, NULL, "
        "e.confidence, e.quote, e.file_id, e.chunk_id, e.page_start, e.page_end "
        "FROM events e "
        "JOIN event_cases ec ON ec.event_id=e.event_id "
        "WHERE ec.case_id_norm=? "
        "ORDER BY e.event_id ASC "
        "LIMIT ?",
        (case_id_norm, int(limit)),
    ).fetchall()


def build_case_timeline(db_path: str, case_id_norm: str, limit: int = 500) -> TimelineResult:
    case_key = (case_id_norm or "").strip()
    if not case_key:
        return TimelineResult(
            status=404,
            code="case_not_found",
            case_id_norm="",
            normalized_rows=[],
            unresolved_rows=[],
            widget_states=[build_widget_error("timeline", "invalid_case_id")],
        )

    conn = connect_db(db_path)
    try:
        if not (_table_exists(conn, "events") and _table_exists(conn, "event_cases")):
            return TimelineResult(
                status=404,
                code="case_not_found",
                case_id_norm=case_key,
                normalized_rows=[],
                unresolved_rows=[],
                widget_states=[build_widget_error("timeline", "missing_required_tables")],
            )

        has_case = conn.execute(
            "SELECT 1 FROM event_cases WHERE case_id_norm=? LIMIT 1",
            (case_key,),
        ).fetchone()
        if not has_case:
            return TimelineResult(
                status=404,
                code="case_not_found",
                case_id_norm=case_key,
                normalized_rows=[],
                unresolved_rows=[],
                widget_states=[build_widget_error("timeline", "case_not_found")],
            )

        raw_rows = _load_rows(conn, case_key, limit=limit)
        rows = [
            TimelineEventRow(
                event_id=int(event_id),
                event=str(event),
                date_raw=str(date_raw) if isinstance(date_raw, str) else None,
                date_start=str(date_start) if isinstance(date_start, str) else None,
                date_end=str(date_end) if isinstance(date_end, str) else None,
                precision=str(precision) if isinstance(precision, str) else None,
                status=str(status),
                parser=str(parser) if isinstance(parser, str) else None,
                anchor_date=str(anchor_date) if isinstance(anchor_date, str) else None,
                confidence=float(confidence) if confidence is not None else None,
                quote=str(quote) if isinstance(quote, str) else None,
                provenance=TimelineProvenance(
                    source_table="events",
                    record_id=int(event_id),
                    file_id=str(file_id),
                    chunk_id=str(chunk_id),
                    page_start=int(page_start) if page_start is not None else None,
                    page_end=int(page_end) if page_end is not None else None,
                ),
            )
            for (
                event_id,
                event,
                date_raw,
                date_start,
                date_end,
                precision,
                status,
                parser,
                anchor_date,
                confidence,
                quote,
                file_id,
                chunk_id,
                page_start,
                page_end,
            ) in raw_rows
        ]

        normalized_rows = sorted(
            (row for row in rows if row.status == "ok" and row.date_start),
            key=lambda row: (row.date_start, row.event_id),
        )
        unresolved_rows = sorted(
            (row for row in rows if not (row.status == "ok" and row.date_start)),
            key=lambda row: (row.status, row.date_raw or "", row.event_id),
        )

        widget_states = [
            WidgetState(widget_id="normalized_lane", status="ready" if normalized_rows else "empty"),
            WidgetState(widget_id="unresolved_lane", status="ready" if unresolved_rows else "empty"),
            WidgetState(widget_id="source_drilldown", status="ready" if rows else "empty"),
        ]

        return TimelineResult(
            status=200,
            code="ok",
            case_id_norm=case_key,
            normalized_rows=normalized_rows,
            unresolved_rows=unresolved_rows,
            widget_states=widget_states,
        )
    finally:
        conn.close()

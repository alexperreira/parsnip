from dataclasses import dataclass
from typing import Optional

from loaders.store import connect_db

from file_parser.ui_shell import SharedFilterState, WidgetState, build_widget_error, resolve_case_filter


@dataclass(frozen=True)
class EvidenceItem:
    source_table: str
    record_id: int
    file_id: str
    chunk_id: str
    page_start: Optional[int]
    page_end: Optional[int]
    confidence: Optional[float]
    quote: Optional[str]
    date_start: Optional[str]
    case_id_norm: str


@dataclass(frozen=True)
class EvidencePage:
    page: int
    page_size: int
    total_rows: int
    total_pages: int
    sort_by: str
    sort_dir: str
    items: list[EvidenceItem]


@dataclass(frozen=True)
class EvidenceFilter:
    source_table: Optional[str] = None
    confidence_min: Optional[float] = None
    confidence_max: Optional[float] = None
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    query: Optional[str] = None


@dataclass(frozen=True)
class EvidenceResult:
    status: int
    code: str
    case_id_norm: str
    evidence_page: Optional[EvidencePage]
    widget_states: list[WidgetState]


_SORT_ALLOW_LIST = {
    "file_id": "u.file_id",
    "chunk_id": "u.chunk_id",
    "page_start": "COALESCE(u.page_start, 0)",
    "confidence": "COALESCE(u.confidence, -1.0)",
    "date_start": "COALESCE(u.date_start, '')",
    "source_table": "u.source_table",
}

_SOURCE_TABLES = ("entities", "events", "conversations", "identity_signals")


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _normalize_page(value: int) -> int:
    return int(value) if int(value) >= 1 else 1


def _normalize_page_size(value: int) -> int:
    allowed = (10, 25, 50, 100)
    return int(value) if int(value) in allowed else 25


def _normalize_sort(sort_by: str, sort_dir: str) -> tuple[str, str]:
    normalized_by = sort_by if sort_by in _SORT_ALLOW_LIST else "page_start"
    normalized_dir = "desc" if str(sort_dir).lower() == "desc" else "asc"
    return normalized_by, normalized_dir


def _build_union_sql(include_date: bool = True) -> str:
    date_col_entities = "NULL AS date_start"
    date_col_conversations = "NULL AS date_start"
    date_col_signals = "NULL AS date_start"
    date_col_events = "et.date_start AS date_start" if include_date else "NULL AS date_start"
    return (
        "SELECT 'entities' AS source_table, e.entity_id AS record_id, e.file_id, e.chunk_id, "
        "e.page_start, e.page_end, e.confidence, e.quote, "
        f"{date_col_entities} "
        "FROM entities e "
        "UNION ALL "
        "SELECT 'events' AS source_table, e.event_id AS record_id, e.file_id, e.chunk_id, "
        "e.page_start, e.page_end, e.confidence, e.quote, "
        f"{date_col_events} "
        "FROM events e LEFT JOIN event_times et ON et.event_id=e.event_id "
        "UNION ALL "
        "SELECT 'conversations' AS source_table, c.conversation_id AS record_id, c.file_id, c.chunk_id, "
        "c.page_start, c.page_end, c.confidence, c.quote, "
        f"{date_col_conversations} "
        "FROM conversations c "
        "UNION ALL "
        "SELECT 'identity_signals' AS source_table, s.signal_id AS record_id, s.file_id, s.chunk_id, "
        "s.page_start, s.page_end, s.confidence, s.quote, "
        f"{date_col_signals} "
        "FROM identity_signals s"
    )


def build_evidence_browser(
    db_path: str,
    case_id_norm: str,
    page: int = 1,
    page_size: int = 25,
    sort_by: str = "page_start",
    sort_dir: str = "asc",
    filters: Optional[EvidenceFilter] = None,
    shared_filters: Optional[SharedFilterState] = None,
) -> EvidenceResult:
    case_key = (resolve_case_filter(case_id_norm, shared_filters) or "").strip()
    if not case_key:
        return EvidenceResult(
            status=404,
            code="case_not_found",
            case_id_norm="",
            evidence_page=None,
            widget_states=[build_widget_error("evidence_browser", "invalid_case_id")],
        )

    conn = connect_db(db_path)
    try:
        if not (_table_exists(conn, "events") and _table_exists(conn, "event_cases")):
            return EvidenceResult(
                status=404,
                code="case_not_found",
                case_id_norm=case_key,
                evidence_page=None,
                widget_states=[build_widget_error("evidence_browser", "missing_required_tables")],
            )

        has_case = conn.execute(
            "SELECT 1 FROM event_cases WHERE case_id_norm=? LIMIT 1",
            (case_key,),
        ).fetchone()
        if not has_case:
            return EvidenceResult(
                status=404,
                code="case_not_found",
                case_id_norm=case_key,
                evidence_page=None,
                widget_states=[build_widget_error("evidence_browser", "case_not_found")],
            )

        f = filters or EvidenceFilter()
        f = EvidenceFilter(
            source_table=f.source_table,
            confidence_min=f.confidence_min if f.confidence_min is not None else (shared_filters.confidence_min if shared_filters else None),
            confidence_max=f.confidence_max,
            date_start=f.date_start if f.date_start is not None else (shared_filters.date_start if shared_filters else None),
            date_end=f.date_end if f.date_end is not None else (shared_filters.date_end if shared_filters else None),
            query=f.query,
        )
        page_n = _normalize_page(page)
        page_size_n = _normalize_page_size(page_size)
        sort_by_n, sort_dir_n = _normalize_sort(sort_by, sort_dir)

        conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS ui_case_chunks ("
            "file_id TEXT NOT NULL,"
            "chunk_id TEXT NOT NULL,"
            "PRIMARY KEY(file_id, chunk_id)"
            ")"
        )
        conn.execute("DELETE FROM ui_case_chunks")
        conn.execute(
            "INSERT OR IGNORE INTO ui_case_chunks(file_id, chunk_id) "
            "SELECT DISTINCT e.file_id, e.chunk_id "
            "FROM events e JOIN event_cases ec ON ec.event_id=e.event_id "
            "WHERE ec.case_id_norm=?",
            (case_key,),
        )

        include_tables = [table for table in _SOURCE_TABLES if _table_exists(conn, table)]
        if not include_tables:
            return EvidenceResult(
                status=200,
                code="ok",
                case_id_norm=case_key,
                evidence_page=EvidencePage(
                    page=page_n,
                    page_size=page_size_n,
                    total_rows=0,
                    total_pages=0,
                    sort_by=sort_by_n,
                    sort_dir=sort_dir_n,
                    items=[],
                ),
                widget_states=[WidgetState(widget_id="evidence_table", status="empty")],
            )

        sql = (
            "SELECT u.source_table, u.record_id, u.file_id, u.chunk_id, u.page_start, u.page_end, u.confidence, u.quote, u.date_start "
            "FROM (" + _build_union_sql(include_date=True) + ") u "
            "JOIN ui_case_chunks c ON c.file_id=u.file_id AND c.chunk_id=u.chunk_id "
            "WHERE 1=1"
        )
        params: list = []

        if f.source_table and f.source_table in _SOURCE_TABLES:
            sql += " AND u.source_table=?"
            params.append(f.source_table)
        if f.confidence_min is not None:
            sql += " AND COALESCE(u.confidence, -1.0) >= ?"
            params.append(float(f.confidence_min))
        if f.confidence_max is not None:
            sql += " AND COALESCE(u.confidence, -1.0) <= ?"
            params.append(float(f.confidence_max))
        if isinstance(f.date_start, str) and f.date_start.strip():
            sql += " AND COALESCE(u.date_start, '') >= ?"
            params.append(f.date_start.strip())
        if isinstance(f.date_end, str) and f.date_end.strip():
            sql += " AND COALESCE(u.date_start, '') <= ?"
            params.append(f.date_end.strip())
        if isinstance(f.query, str) and f.query.strip():
            like_value = "%" + f.query.strip().lower() + "%"
            sql += " AND (LOWER(COALESCE(u.quote, '')) LIKE ? OR LOWER(COALESCE(u.file_id, '')) LIKE ? OR LOWER(COALESCE(u.chunk_id, '')) LIKE ?)"
            params.extend([like_value, like_value, like_value])

        count_sql = "SELECT COUNT(*) FROM (" + sql + ") q"
        total_rows_row = conn.execute(count_sql, tuple(params)).fetchone()
        total_rows = int(total_rows_row[0] if total_rows_row and total_rows_row[0] is not None else 0)
        total_pages = (total_rows + page_size_n - 1) // page_size_n if total_rows else 0

        order_expr = _SORT_ALLOW_LIST[sort_by_n]
        sql += f" ORDER BY {order_expr} {sort_dir_n.upper()}, record_id ASC"
        sql += " LIMIT ? OFFSET ?"
        params.extend([page_size_n, (page_n - 1) * page_size_n])
        rows = conn.execute(sql, tuple(params)).fetchall()

        items = [
            EvidenceItem(
                source_table=str(source_table),
                record_id=int(record_id),
                file_id=str(file_id),
                chunk_id=str(chunk_id),
                page_start=int(page_start) if page_start is not None else None,
                page_end=int(page_end) if page_end is not None else None,
                confidence=float(confidence) if confidence is not None else None,
                quote=str(quote) if isinstance(quote, str) else None,
                date_start=str(date_start) if isinstance(date_start, str) else None,
                case_id_norm=case_key,
            )
            for source_table, record_id, file_id, chunk_id, page_start, page_end, confidence, quote, date_start in rows
        ]

        status = "ready" if items else "empty"
        return EvidenceResult(
            status=200,
            code="ok",
            case_id_norm=case_key,
            evidence_page=EvidencePage(
                page=page_n,
                page_size=page_size_n,
                total_rows=total_rows,
                total_pages=total_pages,
                sort_by=sort_by_n,
                sort_dir=sort_dir_n,
                items=items,
            ),
            widget_states=[WidgetState(widget_id="evidence_table", status=status)],
        )
    finally:
        conn.close()

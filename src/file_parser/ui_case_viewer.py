from dataclasses import dataclass
from typing import Optional

from loaders.store import connect_db

from file_parser.ui_shell import SharedFilterState, WidgetState, build_widget_error, resolve_case_filter


@dataclass(frozen=True)
class CaseViewerSummary:
    people_count: int
    event_count: int
    evidence_count: int
    unresolved_date_count: int


@dataclass(frozen=True)
class LinkedEntity:
    entity: str
    ent_type: Optional[str]
    occurrences: int
    max_confidence: float


@dataclass(frozen=True)
class EvidencePointer:
    source_table: str
    record_id: int
    file_id: str
    chunk_id: str
    page_start: Optional[int]
    page_end: Optional[int]
    confidence: Optional[float]
    quote: Optional[str]


@dataclass(frozen=True)
class CaseViewerResult:
    status: int
    code: str
    case_id_norm: str
    case_id_display: Optional[str]
    summary: Optional[CaseViewerSummary]
    linked_entities: list[LinkedEntity]
    evidence: list[EvidencePointer]
    widget_states: list[WidgetState]


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _find_case_display(conn, case_id_norm: str) -> Optional[str]:
    if not _table_exists(conn, "cases"):
        return case_id_norm
    row = conn.execute(
        "SELECT case_id_display FROM cases WHERE case_id_norm=?",
        (case_id_norm,),
    ).fetchone()
    if row and isinstance(row[0], str) and row[0].strip():
        return str(row[0])
    return case_id_norm


def _load_case_chunks(conn, case_id_norm: str) -> int:
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS ui_case_chunks ("
        "file_id TEXT NOT NULL,"
        "chunk_id TEXT NOT NULL,"
        "PRIMARY KEY(file_id, chunk_id)"
        ")"
    )
    conn.execute("DELETE FROM ui_case_chunks")

    if _table_exists(conn, "events") and _table_exists(conn, "event_cases"):
        conn.execute(
            "INSERT OR IGNORE INTO ui_case_chunks(file_id, chunk_id) "
            "SELECT DISTINCT e.file_id, e.chunk_id "
            "FROM events e JOIN event_cases ec ON e.event_id = ec.event_id "
            "WHERE ec.case_id_norm=?",
            (case_id_norm,),
        )

    if _table_exists(conn, "identity_signals"):
        conn.execute(
            "INSERT OR IGNORE INTO ui_case_chunks(file_id, chunk_id) "
            "SELECT DISTINCT file_id, chunk_id FROM identity_signals "
            "WHERE attribute='case_id' AND value_norm=?",
            (case_id_norm,),
        )

    row = conn.execute("SELECT COUNT(*) FROM ui_case_chunks").fetchone()
    return int(row[0] if row and row[0] is not None else 0)


def build_case_view(
    db_path: str,
    case_id_norm: str,
    entity_limit: int = 25,
    evidence_limit: int = 50,
    shared_filters: Optional[SharedFilterState] = None,
) -> CaseViewerResult:
    case_key = (resolve_case_filter(case_id_norm, shared_filters) or "").strip()
    if not case_key:
        return CaseViewerResult(
            status=404,
            code="case_not_found",
            case_id_norm="",
            case_id_display=None,
            summary=None,
            linked_entities=[],
            evidence=[],
            widget_states=[build_widget_error("case_viewer", "invalid_case_id")],
        )

    conn = connect_db(db_path)
    widget_states: list[WidgetState] = []
    try:
        chunk_count = _load_case_chunks(conn, case_key)
        if chunk_count <= 0 and not (
            _table_exists(conn, "cases")
            and conn.execute(
                "SELECT 1 FROM cases WHERE case_id_norm=? LIMIT 1",
                (case_key,),
            ).fetchone()
        ):
            return CaseViewerResult(
                status=404,
                code="case_not_found",
                case_id_norm=case_key,
                case_id_display=None,
                summary=None,
                linked_entities=[],
                evidence=[],
                widget_states=[build_widget_error("case_viewer", "case_not_found")],
            )

        case_id_display = _find_case_display(conn, case_key)
        confidence_min = shared_filters.confidence_min if shared_filters else None
        date_start = shared_filters.date_start if shared_filters else None
        date_end = shared_filters.date_end if shared_filters else None

        summary: Optional[CaseViewerSummary] = None
        if _table_exists(conn, "events") and _table_exists(conn, "event_cases"):
            event_filters_sql = ""
            event_params: list = [case_key]
            if confidence_min is not None:
                event_filters_sql += " AND COALESCE(e.confidence, -1.0) >= ?"
                event_params.append(float(confidence_min))
            if isinstance(date_start, str) and date_start.strip():
                event_filters_sql += " AND COALESCE(et.date_start, '') >= ?"
                event_params.append(date_start.strip())
            if isinstance(date_end, str) and date_end.strip():
                event_filters_sql += " AND COALESCE(et.date_start, '') <= ?"
                event_params.append(date_end.strip())

            event_count_row = conn.execute(
                "SELECT COUNT(DISTINCT ec.event_id) "
                "FROM event_cases ec "
                "JOIN events e ON e.event_id=ec.event_id "
                "LEFT JOIN event_times et ON et.event_id=ec.event_id "
                "WHERE ec.case_id_norm=?" + event_filters_sql,
                tuple(event_params),
            ).fetchone()
            unresolved_row = conn.execute(
                "SELECT COUNT(*) "
                "FROM event_cases ec "
                "JOIN events e ON e.event_id=ec.event_id "
                "LEFT JOIN event_times et ON et.event_id=ec.event_id "
                "WHERE ec.case_id_norm=? AND COALESCE(et.status, 'missing') != 'ok'" + event_filters_sql,
                tuple(event_params),
            ).fetchone()
            people_sql = (
                "SELECT COUNT(DISTINCT LOWER(TRIM(e.entity))) "
                "FROM entities e JOIN ui_case_chunks c "
                "ON e.file_id = c.file_id AND e.chunk_id = c.chunk_id "
                "WHERE LOWER(COALESCE(e.type, ''))='person' AND TRIM(e.entity) != ''"
            )
            people_params: list = []
            if confidence_min is not None:
                people_sql += " AND COALESCE(e.confidence, -1.0) >= ?"
                people_params.append(float(confidence_min))
            people_row = conn.execute(people_sql, tuple(people_params)).fetchone() if _table_exists(conn, "entities") else (0,)

            evidence_count = 0
            for table in ("entities", "events", "conversations", "identity_signals"):
                if not _table_exists(conn, table):
                    continue
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {table} t JOIN ui_case_chunks c "
                    "ON t.file_id=c.file_id AND t.chunk_id=c.chunk_id"
                    + (" WHERE COALESCE(t.confidence, -1.0) >= ?" if confidence_min is not None else ""),
                    (float(confidence_min),) if confidence_min is not None else (),
                ).fetchone()
                evidence_count += int(row[0] if row and row[0] is not None else 0)

            summary = CaseViewerSummary(
                people_count=int(people_row[0] if people_row and people_row[0] is not None else 0),
                event_count=int(event_count_row[0] if event_count_row and event_count_row[0] is not None else 0),
                evidence_count=evidence_count,
                unresolved_date_count=int(unresolved_row[0] if unresolved_row and unresolved_row[0] is not None else 0),
            )
            widget_states.append(WidgetState(widget_id="summary", status="ready"))
        else:
            widget_states.append(build_widget_error("summary", "missing_required_tables"))

        linked_entities: list[LinkedEntity] = []
        if _table_exists(conn, "entities"):
            entities_sql = (
                "SELECT e.entity, e.type, COUNT(*) AS occurrences, MAX(COALESCE(e.confidence, 0.0)) AS max_conf "
                + "FROM entities e JOIN ui_case_chunks c "
                + "ON e.file_id=c.file_id AND e.chunk_id=c.chunk_id "
                + "WHERE TRIM(e.entity) != '' "
                + ("AND COALESCE(e.confidence, -1.0) >= ? " if confidence_min is not None else "")
                + "GROUP BY e.entity, e.type "
                + "ORDER BY occurrences DESC, LOWER(e.entity) ASC, LOWER(COALESCE(e.type, '')) ASC "
                + "LIMIT ?"
            )
            rows = conn.execute(
                entities_sql,
                ((float(confidence_min), int(entity_limit)) if confidence_min is not None else (int(entity_limit),)),
            ).fetchall()
            linked_entities = [
                LinkedEntity(
                    entity=str(entity),
                    ent_type=str(ent_type) if isinstance(ent_type, str) else None,
                    occurrences=int(occurrences),
                    max_confidence=float(max_conf),
                )
                for entity, ent_type, occurrences, max_conf in rows
            ]
            widget_states.append(
                WidgetState(widget_id="linked_entities", status="ready" if linked_entities else "empty")
            )
        else:
            widget_states.append(build_widget_error("linked_entities", "missing_entities_table"))

        evidence: list[EvidencePointer] = []
        evidence_tables = ("entities", "events", "conversations", "identity_signals")
        if all(_table_exists(conn, table) for table in evidence_tables):
            evidence_sql = (
                "SELECT source_table, record_id, file_id, chunk_id, page_start, page_end, confidence, quote "
                + "FROM ("
                + "SELECT 'entities' AS source_table, e.entity_id AS record_id, e.file_id, e.chunk_id, e.page_start, e.page_end, e.confidence, e.quote "
                + "FROM entities e JOIN ui_case_chunks c ON e.file_id=c.file_id AND e.chunk_id=c.chunk_id "
                + "UNION ALL "
                + "SELECT 'events' AS source_table, e.event_id AS record_id, e.file_id, e.chunk_id, e.page_start, e.page_end, e.confidence, e.quote "
                + "FROM events e JOIN ui_case_chunks c ON e.file_id=c.file_id AND e.chunk_id=c.chunk_id "
                + "UNION ALL "
                + "SELECT 'conversations' AS source_table, e.conversation_id AS record_id, e.file_id, e.chunk_id, e.page_start, e.page_end, e.confidence, e.quote "
                + "FROM conversations e JOIN ui_case_chunks c ON e.file_id=c.file_id AND e.chunk_id=c.chunk_id "
                + "UNION ALL "
                + "SELECT 'identity_signals' AS source_table, e.signal_id AS record_id, e.file_id, e.chunk_id, e.page_start, e.page_end, e.confidence, e.quote "
                + "FROM identity_signals e JOIN ui_case_chunks c ON e.file_id=c.file_id AND e.chunk_id=c.chunk_id"
                + ") "
                + ("WHERE COALESCE(confidence, -1.0) >= ? " if confidence_min is not None else "")
                + "ORDER BY file_id ASC, chunk_id ASC, COALESCE(page_start, 0) ASC, COALESCE(page_end, 0) ASC, source_table ASC, record_id ASC "
                + "LIMIT ?"
            )
            rows = conn.execute(
                evidence_sql,
                ((float(confidence_min), int(evidence_limit)) if confidence_min is not None else (int(evidence_limit),)),
            ).fetchall()
            evidence = [
                EvidencePointer(
                    source_table=str(source_table),
                    record_id=int(record_id),
                    file_id=str(file_id),
                    chunk_id=str(chunk_id),
                    page_start=int(page_start) if page_start is not None else None,
                    page_end=int(page_end) if page_end is not None else None,
                    confidence=float(confidence) if confidence is not None else None,
                    quote=str(quote) if isinstance(quote, str) else None,
                )
                for source_table, record_id, file_id, chunk_id, page_start, page_end, confidence, quote in rows
            ]
            widget_states.append(WidgetState(widget_id="recent_evidence", status="ready" if evidence else "empty"))
        else:
            widget_states.append(build_widget_error("recent_evidence", "missing_evidence_tables"))

        return CaseViewerResult(
            status=200,
            code="ok",
            case_id_norm=case_key,
            case_id_display=case_id_display,
            summary=summary,
            linked_entities=linked_entities,
            evidence=evidence,
            widget_states=widget_states,
        )
    finally:
        conn.close()

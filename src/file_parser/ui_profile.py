from dataclasses import dataclass
from typing import Optional

from loaders.store import connect_db

from file_parser.ui_shell import SharedFilterState, WidgetState, build_widget_error, resolve_case_filter


@dataclass(frozen=True)
class MergeStatus:
    auto_merge: int
    needs_review: int
    no_merge: int


@dataclass(frozen=True)
class ProfileIdentity:
    person_id: int
    display_name: str
    display_name_norm: str
    dob: Optional[str]
    aliases: list[str]
    merge_status: MergeStatus


@dataclass(frozen=True)
class LinkedEvent:
    event_id: int
    event: str
    date_raw: Optional[str]
    date_start: Optional[str]
    status: str
    confidence: Optional[float]
    file_id: str
    chunk_id: str
    page_start: Optional[int]
    page_end: Optional[int]


@dataclass(frozen=True)
class ProfileEvidencePointer:
    source_table: str
    record_id: int
    file_id: str
    chunk_id: str
    page_start: Optional[int]
    page_end: Optional[int]
    confidence: Optional[float]
    quote: Optional[str]


@dataclass(frozen=True)
class ProfileResult:
    status: int
    code: str
    case_id_norm: str
    person_id: int
    identity: Optional[ProfileIdentity]
    linked_events: list[LinkedEvent]
    linked_evidence: list[ProfileEvidencePointer]
    widget_states: list[WidgetState]


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _prepare_person_case_chunks(conn, case_id_norm: str, person_id: int) -> int:
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS ui_person_obs_chunks ("
        "file_id TEXT NOT NULL,"
        "chunk_id TEXT NOT NULL,"
        "PRIMARY KEY(file_id, chunk_id)"
        ")"
    )
    conn.execute("DELETE FROM ui_person_obs_chunks")
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS ui_person_case_chunks ("
        "file_id TEXT NOT NULL,"
        "chunk_id TEXT NOT NULL,"
        "PRIMARY KEY(file_id, chunk_id)"
        ")"
    )
    conn.execute("DELETE FROM ui_person_case_chunks")

    conn.execute(
        "INSERT OR IGNORE INTO ui_person_obs_chunks(file_id, chunk_id) "
        "SELECT DISTINCT o.file_id, o.chunk_id "
        "FROM person_cluster_members m "
        "JOIN person_observations o ON o.obs_id = m.obs_id "
        "WHERE m.person_id=?",
        (int(person_id),),
    )

    if _table_exists(conn, "identity_signals"):
        conn.execute(
            "INSERT OR IGNORE INTO ui_person_case_chunks(file_id, chunk_id) "
            "SELECT DISTINCT p.file_id, p.chunk_id "
            "FROM ui_person_obs_chunks p "
            "JOIN identity_signals s ON s.file_id=p.file_id AND s.chunk_id=p.chunk_id "
            "WHERE s.attribute='case_id' AND s.value_norm=?",
            (case_id_norm,),
        )

    if _table_exists(conn, "events") and _table_exists(conn, "event_cases"):
        conn.execute(
            "INSERT OR IGNORE INTO ui_person_case_chunks(file_id, chunk_id) "
            "SELECT DISTINCT p.file_id, p.chunk_id "
            "FROM ui_person_obs_chunks p "
            "JOIN events e ON e.file_id=p.file_id AND e.chunk_id=p.chunk_id "
            "JOIN event_cases ec ON ec.event_id=e.event_id "
            "WHERE ec.case_id_norm=?",
            (case_id_norm,),
        )

    row = conn.execute("SELECT COUNT(*) FROM ui_person_case_chunks").fetchone()
    return int(row[0] if row and row[0] is not None else 0)


def build_person_profile(
    db_path: str,
    case_id_norm: str,
    person_id: int,
    event_limit: int = 100,
    evidence_limit: int = 100,
    shared_filters: Optional[SharedFilterState] = None,
) -> ProfileResult:
    case_key = (resolve_case_filter(case_id_norm, shared_filters) or "").strip()
    effective_person_id = int(shared_filters.person_id) if shared_filters and shared_filters.person_id else int(person_id)
    if not case_key or effective_person_id <= 0:
        return ProfileResult(
            status=404,
            code="person_not_found",
            case_id_norm=case_key,
            person_id=effective_person_id,
            identity=None,
            linked_events=[],
            linked_evidence=[],
            widget_states=[build_widget_error("profile", "invalid_profile_params")],
        )

    conn = connect_db(db_path)
    widget_states: list[WidgetState] = []
    try:
        if not (
            _table_exists(conn, "person_clusters")
            and _table_exists(conn, "person_cluster_members")
            and _table_exists(conn, "person_observations")
        ):
            return ProfileResult(
                status=404,
                code="person_not_found",
                case_id_norm=case_key,
                person_id=effective_person_id,
                identity=None,
                linked_events=[],
                linked_evidence=[],
                widget_states=[build_widget_error("profile", "missing_person_tables")],
            )

        cluster_row = conn.execute(
            "SELECT person_id, display_name, display_name_norm, dob "
            "FROM person_clusters WHERE person_id=?",
            (effective_person_id,),
        ).fetchone()
        if not cluster_row:
            return ProfileResult(
                status=404,
                code="person_not_found",
                case_id_norm=case_key,
                person_id=effective_person_id,
                identity=None,
                linked_events=[],
                linked_evidence=[],
                widget_states=[build_widget_error("profile", "person_not_found")],
            )

        case_chunk_count = _prepare_person_case_chunks(conn, case_key, effective_person_id)
        if case_chunk_count <= 0:
            return ProfileResult(
                status=404,
                code="person_not_in_case",
                case_id_norm=case_key,
                person_id=effective_person_id,
                identity=None,
                linked_events=[],
                linked_evidence=[],
                widget_states=[build_widget_error("profile", "person_not_in_case")],
            )

        aliases_rows = conn.execute(
            "SELECT DISTINCT o.name "
            "FROM person_cluster_members m JOIN person_observations o ON o.obs_id=m.obs_id "
            "JOIN ui_person_case_chunks c ON c.file_id=o.file_id AND c.chunk_id=o.chunk_id "
            "WHERE m.person_id=? AND TRIM(o.name) != '' "
            "ORDER BY LOWER(o.name) ASC, o.name ASC",
            (effective_person_id,),
        ).fetchall()
        aliases = [str(name) for (name,) in aliases_rows if isinstance(name, str) and name.strip()]

        merge_counts = {"auto_merge": 0, "needs_review": 0, "no_merge": 0}
        if _table_exists(conn, "person_resolution_edges"):
            merge_rows = conn.execute(
                "SELECT r.decision, COUNT(*) "
                "FROM person_resolution_edges r "
                "JOIN person_cluster_members lm ON lm.obs_id=r.left_obs_id "
                "JOIN person_cluster_members rm ON rm.obs_id=r.right_obs_id "
                "WHERE lm.person_id=? AND rm.person_id=? "
                "GROUP BY r.decision",
                (effective_person_id, effective_person_id),
            ).fetchall()
            for decision, count in merge_rows:
                if isinstance(decision, str) and decision in merge_counts:
                    merge_counts[decision] = int(count)

        identity = ProfileIdentity(
            person_id=effective_person_id,
            display_name=str(cluster_row[1]),
            display_name_norm=str(cluster_row[2]),
            dob=str(cluster_row[3]) if isinstance(cluster_row[3], str) and cluster_row[3].strip() else None,
            aliases=aliases,
            merge_status=MergeStatus(
                auto_merge=merge_counts["auto_merge"],
                needs_review=merge_counts["needs_review"],
                no_merge=merge_counts["no_merge"],
            ),
        )
        widget_states.append(WidgetState(widget_id="identity", status="ready"))

        linked_events: list[LinkedEvent] = []
        confidence_min = shared_filters.confidence_min if shared_filters else None
        date_start = shared_filters.date_start if shared_filters else None
        date_end = shared_filters.date_end if shared_filters else None
        if _table_exists(conn, "events") and _table_exists(conn, "event_cases"):
            event_filter_sql = ""
            event_filter_params: list = []
            if confidence_min is not None:
                event_filter_sql += " AND COALESCE(e.confidence, -1.0) >= ?"
                event_filter_params.append(float(confidence_min))
            if isinstance(date_start, str) and date_start.strip():
                event_filter_sql += " AND COALESCE(et.date_start, '') >= ?"
                event_filter_params.append(date_start.strip())
            if isinstance(date_end, str) and date_end.strip():
                event_filter_sql += " AND COALESCE(et.date_start, '') <= ?"
                event_filter_params.append(date_end.strip())
            rows = conn.execute(
                "SELECT e.event_id, e.event, e.date, et.date_start, COALESCE(et.status, 'missing') AS status, "
                "e.confidence, e.file_id, e.chunk_id, e.page_start, e.page_end "
                "FROM events e "
                "JOIN event_cases ec ON ec.event_id=e.event_id "
                "JOIN ui_person_case_chunks c ON c.file_id=e.file_id AND c.chunk_id=e.chunk_id "
                "LEFT JOIN event_times et ON et.event_id=e.event_id "
                "WHERE ec.case_id_norm=? " + event_filter_sql +
                "ORDER BY CASE WHEN COALESCE(et.status, 'missing')='ok' THEN 0 ELSE 1 END ASC, "
                "COALESCE(et.date_start, '') ASC, e.event_id ASC "
                "LIMIT ?",
                tuple([case_key] + event_filter_params + [int(event_limit)]),
            ).fetchall()
            linked_events = [
                LinkedEvent(
                    event_id=int(event_id),
                    event=str(event),
                    date_raw=str(date_raw) if isinstance(date_raw, str) else None,
                    date_start=str(date_start) if isinstance(date_start, str) else None,
                    status=str(status),
                    confidence=float(confidence) if confidence is not None else None,
                    file_id=str(file_id),
                    chunk_id=str(chunk_id),
                    page_start=int(page_start) if page_start is not None else None,
                    page_end=int(page_end) if page_end is not None else None,
                )
                for event_id, event, date_raw, date_start, status, confidence, file_id, chunk_id, page_start, page_end in rows
            ]
            widget_states.append(WidgetState(widget_id="linked_events", status="ready" if linked_events else "empty"))
        else:
            widget_states.append(build_widget_error("linked_events", "missing_event_tables"))

        linked_evidence: list[ProfileEvidencePointer] = []
        evidence_sources = ("entities", "events", "conversations", "identity_signals")
        if any(_table_exists(conn, table) for table in evidence_sources):
            union_parts = []
            if _table_exists(conn, "entities"):
                union_parts.append(
                    "SELECT 'entities' AS source_table, e.entity_id AS record_id, e.file_id, e.chunk_id, "
                    "e.page_start, e.page_end, e.confidence, e.quote "
                    "FROM entities e JOIN ui_person_case_chunks c ON c.file_id=e.file_id AND c.chunk_id=e.chunk_id "
                    "JOIN person_cluster_members m ON m.person_id=? "
                    "JOIN person_observations o ON o.obs_id=m.obs_id "
                    "WHERE LOWER(TRIM(e.entity))=LOWER(TRIM(o.name))"
                )
            if _table_exists(conn, "events"):
                union_parts.append(
                    "SELECT 'events' AS source_table, e.event_id AS record_id, e.file_id, e.chunk_id, "
                    "e.page_start, e.page_end, e.confidence, e.quote "
                    "FROM events e JOIN ui_person_case_chunks c ON c.file_id=e.file_id AND c.chunk_id=e.chunk_id"
                )
            if _table_exists(conn, "conversations"):
                union_parts.append(
                    "SELECT 'conversations' AS source_table, e.conversation_id AS record_id, e.file_id, e.chunk_id, "
                    "e.page_start, e.page_end, e.confidence, e.quote "
                    "FROM conversations e JOIN ui_person_case_chunks c ON c.file_id=e.file_id AND c.chunk_id=e.chunk_id"
                )
            if _table_exists(conn, "identity_signals"):
                union_parts.append(
                    "SELECT 'identity_signals' AS source_table, e.signal_id AS record_id, e.file_id, e.chunk_id, "
                    "e.page_start, e.page_end, e.confidence, e.quote "
                    "FROM identity_signals e JOIN ui_person_case_chunks c ON c.file_id=e.file_id AND c.chunk_id=e.chunk_id"
                )

            if union_parts:
                sql = (
                    "SELECT source_table, record_id, file_id, chunk_id, page_start, page_end, confidence, quote "
                    "FROM (" + " UNION ALL ".join(union_parts) + ") "
                    + ("WHERE COALESCE(confidence, -1.0) >= ? " if confidence_min is not None else "")
                    + "ORDER BY file_id ASC, chunk_id ASC, COALESCE(page_start, 0) ASC, "
                    + "COALESCE(page_end, 0) ASC, source_table ASC, record_id ASC "
                    + "LIMIT ?"
                )
                params = [effective_person_id] if _table_exists(conn, "entities") else []
                if confidence_min is not None:
                    params.append(float(confidence_min))
                params.append(int(evidence_limit))
                rows = conn.execute(sql, tuple(params)).fetchall()
                linked_evidence = [
                    ProfileEvidencePointer(
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
            widget_states.append(
                WidgetState(widget_id="linked_evidence", status="ready" if linked_evidence else "empty")
            )
        else:
            widget_states.append(build_widget_error("linked_evidence", "missing_evidence_tables"))

        return ProfileResult(
            status=200,
            code="ok",
            case_id_norm=case_key,
            person_id=effective_person_id,
            identity=identity,
            linked_events=linked_events,
            linked_evidence=linked_evidence,
            widget_states=widget_states,
        )
    finally:
        conn.close()

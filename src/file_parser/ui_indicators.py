from dataclasses import dataclass
from typing import Optional

from loaders.store import connect_db

from file_parser.ui_shell import WidgetState, build_widget_error


@dataclass(frozen=True)
class DedupeIndicators:
    people_clusters: int
    merge_auto: int
    merge_needs_review: int
    merge_no_merge: int


@dataclass(frozen=True)
class ThreadIndicators:
    threads_total: int
    segments_total: int
    participants_total: int
    top_thread_ids: list[int]


@dataclass(frozen=True)
class IndicatorsResult:
    status: int
    code: str
    case_id_norm: str
    dedupe: Optional[DedupeIndicators]
    threading: Optional[ThreadIndicators]
    widget_states: list[WidgetState]


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _prepare_case_chunks(conn, case_id_norm: str) -> int:
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


def build_case_indicators(db_path: str, case_id_norm: str) -> IndicatorsResult:
    case_key = (case_id_norm or "").strip()
    if not case_key:
        return IndicatorsResult(
            status=404,
            code="case_not_found",
            case_id_norm="",
            dedupe=None,
            threading=None,
            widget_states=[build_widget_error("indicators", "invalid_case_id")],
        )

    conn = connect_db(db_path)
    widget_states: list[WidgetState] = []
    try:
        if not (_table_exists(conn, "events") and _table_exists(conn, "event_cases")):
            return IndicatorsResult(
                status=404,
                code="case_not_found",
                case_id_norm=case_key,
                dedupe=None,
                threading=None,
                widget_states=[build_widget_error("indicators", "missing_required_tables")],
            )

        has_case = conn.execute(
            "SELECT 1 FROM event_cases WHERE case_id_norm=? LIMIT 1",
            (case_key,),
        ).fetchone()
        if not has_case:
            return IndicatorsResult(
                status=404,
                code="case_not_found",
                case_id_norm=case_key,
                dedupe=None,
                threading=None,
                widget_states=[build_widget_error("indicators", "case_not_found")],
            )

        _prepare_case_chunks(conn, case_key)

        dedupe: Optional[DedupeIndicators] = None
        dedupe_tables = (
            _table_exists(conn, "person_clusters")
            and _table_exists(conn, "person_cluster_members")
            and _table_exists(conn, "person_observations")
            and _table_exists(conn, "person_resolution_edges")
        )
        if dedupe_tables:
            people_clusters_row = conn.execute(
                "SELECT COUNT(DISTINCT m.person_id) "
                "FROM person_cluster_members m "
                "JOIN person_observations o ON o.obs_id=m.obs_id "
                "JOIN ui_case_chunks c ON c.file_id=o.file_id AND c.chunk_id=o.chunk_id"
            ).fetchone()
            merge_rows = conn.execute(
                "SELECT r.decision, COUNT(*) "
                "FROM person_resolution_edges r "
                "JOIN person_cluster_members lm ON lm.obs_id=r.left_obs_id "
                "JOIN person_cluster_members rm ON rm.obs_id=r.right_obs_id "
                "JOIN person_observations lo ON lo.obs_id=lm.obs_id "
                "JOIN person_observations ro ON ro.obs_id=rm.obs_id "
                "JOIN ui_case_chunks c1 ON c1.file_id=lo.file_id AND c1.chunk_id=lo.chunk_id "
                "JOIN ui_case_chunks c2 ON c2.file_id=ro.file_id AND c2.chunk_id=ro.chunk_id "
                "GROUP BY r.decision"
            ).fetchall()
            counts = {"auto_merge": 0, "needs_review": 0, "no_merge": 0}
            for decision, count in merge_rows:
                if isinstance(decision, str) and decision in counts:
                    counts[decision] = int(count)
            dedupe = DedupeIndicators(
                people_clusters=int(
                    people_clusters_row[0] if people_clusters_row and people_clusters_row[0] is not None else 0
                ),
                merge_auto=counts["auto_merge"],
                merge_needs_review=counts["needs_review"],
                merge_no_merge=counts["no_merge"],
            )
            widget_states.append(WidgetState(widget_id="dedupe_indicators", status="ready"))
        else:
            widget_states.append(build_widget_error("dedupe_indicators", "missing_dedupe_tables"))

        threading: Optional[ThreadIndicators] = None
        thread_tables = (
            _table_exists(conn, "conversation_threads")
            and _table_exists(conn, "conversation_thread_segments")
            and _table_exists(conn, "conversation_segments")
            and _table_exists(conn, "conversation_thread_participants")
        )
        if thread_tables:
            threads_row = conn.execute(
                "SELECT COUNT(*) FROM conversation_threads WHERE case_id_norm=?",
                (case_key,),
            ).fetchone()
            segments_row = conn.execute(
                "SELECT COUNT(*) "
                "FROM conversation_thread_segments ts "
                "JOIN conversation_threads t ON t.thread_id=ts.thread_id "
                "WHERE t.case_id_norm=?",
                (case_key,),
            ).fetchone()
            participants_row = conn.execute(
                "SELECT COUNT(*) "
                "FROM conversation_thread_participants p "
                "JOIN conversation_threads t ON t.thread_id=p.thread_id "
                "WHERE t.case_id_norm=?",
                (case_key,),
            ).fetchone()
            top_rows = conn.execute(
                "SELECT t.thread_id, COUNT(ts.segment_id) AS seg_count "
                "FROM conversation_threads t "
                "LEFT JOIN conversation_thread_segments ts ON ts.thread_id=t.thread_id "
                "WHERE t.case_id_norm=? "
                "GROUP BY t.thread_id "
                "ORDER BY seg_count DESC, t.thread_id ASC "
                "LIMIT 5",
                (case_key,),
            ).fetchall()
            threading = ThreadIndicators(
                threads_total=int(threads_row[0] if threads_row and threads_row[0] is not None else 0),
                segments_total=int(segments_row[0] if segments_row and segments_row[0] is not None else 0),
                participants_total=int(
                    participants_row[0] if participants_row and participants_row[0] is not None else 0
                ),
                top_thread_ids=[int(thread_id) for thread_id, _ in top_rows],
            )
            widget_states.append(WidgetState(widget_id="thread_indicators", status="ready"))
        else:
            widget_states.append(build_widget_error("thread_indicators", "missing_thread_tables"))

        return IndicatorsResult(
            status=200,
            code="ok",
            case_id_norm=case_key,
            dedupe=dedupe,
            threading=threading,
            widget_states=widget_states,
        )
    finally:
        conn.close()

import sqlite3
from pathlib import Path

SCHEMA_VERSION = "4"


def connect_db(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_schema(conn, overwrite=False):
    if overwrite:
        for table in (
            "event_cases",
            "event_times",
            "files",
            "person_resolution_edges",
            "person_cluster_members",
            "person_clusters",
            "person_observations",
            "identity_signals",
            "mentions",
            "entities",
            "events",
            "conversations",
            "meta",
        ):
            conn.execute(f"DROP TABLE IF EXISTS {table}")

    conn.execute(
        "CREATE TABLE IF NOT EXISTS files ("
        "file_id TEXT PRIMARY KEY,"
        "source_type TEXT,"
        "container_path TEXT,"
        "virtual_path TEXT,"
        "mtime_utc TEXT,"
        "size_bytes INTEGER"
        ")"
    )

    conn.execute(
        "CREATE TABLE IF NOT EXISTS person_observations ("
        "obs_id INTEGER PRIMARY KEY,"
        "name TEXT NOT NULL,"
        "name_norm TEXT NOT NULL,"
        "file_id TEXT NOT NULL,"
        "chunk_id TEXT NOT NULL,"
        "page_start INTEGER,"
        "page_end INTEGER,"
        "UNIQUE(name_norm, file_id, chunk_id, page_start, page_end)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS event_times ("
        "event_id INTEGER PRIMARY KEY,"
        "date_raw TEXT,"
        "date_start TEXT,"
        "date_end TEXT,"
        "precision TEXT,"
        "status TEXT NOT NULL,"
        "parser TEXT,"
        "anchor_date TEXT,"
        "notes_json TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS event_cases ("
        "event_id INTEGER NOT NULL,"
        "case_id TEXT NOT NULL,"
        "case_id_norm TEXT NOT NULL,"
        "source TEXT NOT NULL,"
        "PRIMARY KEY(event_id, case_id_norm, source)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS person_clusters ("
        "person_id INTEGER PRIMARY KEY,"
        "display_name TEXT NOT NULL,"
        "display_name_norm TEXT NOT NULL,"
        "dob TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS person_cluster_members ("
        "person_id INTEGER NOT NULL,"
        "obs_id INTEGER NOT NULL,"
        "PRIMARY KEY(person_id, obs_id)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS person_resolution_edges ("
        "left_obs_id INTEGER NOT NULL,"
        "right_obs_id INTEGER NOT NULL,"
        "decision TEXT NOT NULL,"
        "score REAL NOT NULL,"
        "reasons_json TEXT NOT NULL,"
        "PRIMARY KEY(left_obs_id, right_obs_id)"
        ")"
    )

    conn.execute(
        "CREATE TABLE IF NOT EXISTS identity_signals ("
        "signal_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "person_text TEXT NOT NULL,"
        "attribute TEXT NOT NULL,"
        "value TEXT NOT NULL,"
        "value_norm TEXT,"
        "confidence REAL,"
        "file_id TEXT NOT NULL,"
        "chunk_id TEXT NOT NULL,"
        "page_start INTEGER,"
        "page_end INTEGER,"
        "quote TEXT,"
        "UNIQUE(person_text, attribute, value, confidence, file_id, chunk_id, page_start, page_end, quote)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entities ("
        "entity_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "entity TEXT NOT NULL,"
        "type TEXT,"
        "confidence REAL,"
        "file_id TEXT NOT NULL,"
        "chunk_id TEXT NOT NULL,"
        "page_start INTEGER,"
        "page_end INTEGER,"
        "quote TEXT,"
        "UNIQUE(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "event_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "event TEXT NOT NULL,"
        "date TEXT,"
        "confidence REAL,"
        "file_id TEXT NOT NULL,"
        "chunk_id TEXT NOT NULL,"
        "page_start INTEGER,"
        "page_end INTEGER,"
        "quote TEXT,"
        "UNIQUE(event, date, confidence, file_id, chunk_id, page_start, page_end, quote)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS conversations ("
        "conversation_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "speaker TEXT NOT NULL,"
        "confidence REAL,"
        "file_id TEXT NOT NULL,"
        "chunk_id TEXT NOT NULL,"
        "page_start INTEGER,"
        "page_end INTEGER,"
        "quote TEXT,"
        "UNIQUE(speaker, confidence, file_id, chunk_id, page_start, page_end, quote)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mentions ("
        "mention_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "entity TEXT NOT NULL,"
        "file_id TEXT NOT NULL,"
        "chunk_id TEXT NOT NULL,"
        "UNIQUE(entity, file_id, chunk_id)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta ("
        "key TEXT PRIMARY KEY,"
        "value TEXT NOT NULL"
        ")"
    )

    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (SCHEMA_VERSION,),
    )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_entities_file_chunk ON entities(file_id, chunk_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(entity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_file_chunk ON events(file_id, chunk_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(date)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversations_file_chunk ON conversations(file_id, chunk_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mentions_entity ON mentions(entity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mentions_file_chunk ON mentions(file_id, chunk_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_identity_signals_file_chunk "
        "ON identity_signals(file_id, chunk_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_identity_signals_attr_value_norm "
        "ON identity_signals(attribute, value_norm)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_person_observations_chunk "
        "ON person_observations(file_id, chunk_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_person_observations_name_norm "
        "ON person_observations(name_norm)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_person_clusters_name_norm "
        "ON person_clusters(display_name_norm)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_person_clusters_dob "
        "ON person_clusters(dob)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_person_cluster_members_obs "
        "ON person_cluster_members(obs_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime_utc)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_times_start ON event_times(date_start)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_cases_case ON event_cases(case_id_norm)")
    conn.commit()


def mark_loader_run(conn, loader_name):
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (f"loader.{loader_name}.last_run_utc",),
    )


def normalize_page_range(page_range):
    if not isinstance(page_range, (list, tuple)):
        return None, None
    page_start = _as_int(page_range[0]) if len(page_range) > 0 else None
    page_end = _as_int(page_range[1]) if len(page_range) > 1 else None
    return page_start, page_end


def as_clean_text(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value else None


def as_float(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None
    return None


def canonical_quote(value):
    cleaned = as_clean_text(value)
    if cleaned is None:
        return None
    return " ".join(cleaned.split())


def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("-"):
            sign = -1
            digits = value[1:]
        else:
            sign = 1
            digits = value
        if digits.isdigit() and digits:
            return sign * int(digits)
    return None

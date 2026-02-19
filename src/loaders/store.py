import sqlite3
from pathlib import Path

SCHEMA_VERSION = "2"


def connect_db(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_schema(conn, overwrite=False):
    if overwrite:
        for table in (
            "identity_signals",
            "mentions",
            "entities",
            "events",
            "conversations",
            "meta",
        ):
            conn.execute(f"DROP TABLE IF EXISTS {table}")

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

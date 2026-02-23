import hashlib
import json
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def normalize_chunk_text(text: Any) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if "\x00" in text:
        text = text.replace("\x00", "")
    text = unicodedata.normalize("NFC", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return " ".join(text.split())


def chunk_text_hash(text: Any) -> str:
    normalized = normalize_chunk_text(text)
    digest = hashlib.sha256(normalized.encode("utf-8", errors="strict")).hexdigest()
    return digest


def default_cache_db_path(output_path: Any) -> Path:
    out = Path(str(output_path))
    return out.with_suffix(".cache.sqlite")


def connect_cache(db_path: Any) -> sqlite3.Connection:
    path = Path(str(db_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS llm_cache ("
        "  extractor_version TEXT NOT NULL,"
        "  chunk_id TEXT NOT NULL,"
        "  chunk_text_hash TEXT NOT NULL,"
        "  output_json TEXT NOT NULL,"
        "  error TEXT,"
        "  cached_at REAL NOT NULL,"
        "  PRIMARY KEY (extractor_version, chunk_id, chunk_text_hash)"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_cache_chunk_id ON llm_cache(chunk_id)")
    conn.commit()
    return conn


@dataclass(frozen=True)
class CacheLookup:
    hit: bool
    output_record: Optional[Dict[str, Any]]
    error: Optional[str]


def get_cached(
    conn: sqlite3.Connection,
    *,
    extractor_version: str,
    chunk_id: str,
    chunk_text_hash_value: str,
) -> CacheLookup:
    row = conn.execute(
        "SELECT output_json, error FROM llm_cache WHERE extractor_version=? AND chunk_id=? AND chunk_text_hash=?",
        (extractor_version, chunk_id, chunk_text_hash_value),
    ).fetchone()
    if not row:
        return CacheLookup(hit=False, output_record=None, error=None)
    try:
        payload = json.loads(row[0])
    except json.JSONDecodeError:
        return CacheLookup(hit=False, output_record=None, error=None)
    if not isinstance(payload, dict):
        return CacheLookup(hit=False, output_record=None, error=None)
    return CacheLookup(hit=True, output_record=payload, error=row[1])


def put_cached(
    conn: sqlite3.Connection,
    *,
    extractor_version: str,
    chunk_id: str,
    chunk_text_hash_value: str,
    output_record: Dict[str, Any],
) -> None:
    error = output_record.get("error")
    if error is not None and not isinstance(error, str):
        error = str(error)
    conn.execute(
        "INSERT OR REPLACE INTO llm_cache("
        "extractor_version, chunk_id, chunk_text_hash, output_json, error, cached_at"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (
            extractor_version,
            chunk_id,
            chunk_text_hash_value,
            json.dumps(output_record, ensure_ascii=True),
            error,
            time.time(),
        ),
    )


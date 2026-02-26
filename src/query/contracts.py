import hashlib
import re
from dataclasses import dataclass
from typing import Mapping, Optional

UNKNOWN_CASE_ID = "unknown"
MAX_QUOTE_LENGTH = 280

_FORBIDDEN_PATH_CHARS = {"/", "\\", "?", "#", "%", "[", "]", "{", "}", "<", ">", "|", '"', "'", "`"}
_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_PROMPT_HASH_RE = re.compile(r"^[a-f0-9]{32,128}$")
_CASE_ID_INVALID_RE = re.compile(r"[^A-Z0-9._-]+")
_CASE_ID_MULTI_UNDERSCORE_RE = re.compile(r"_+")


def _clean_text(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_optional_text(value) -> Optional[str]:
    text = _clean_text(value)
    if text is None:
        return None
    return " ".join(text.split())


def _coerce_int(value, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError(f"{field_name} must be an integer")


def _coerce_optional_float(value, field_name: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a float")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a float") from exc
    raise ValueError(f"{field_name} must be a float")


def normalize_stable_id(value: str, field_name: str) -> str:
    cleaned = _clean_text(value)
    if cleaned is None:
        raise ValueError(f"{field_name} is required")
    if any(char in _FORBIDDEN_PATH_CHARS for char in cleaned):
        raise ValueError(f"{field_name} contains forbidden characters")
    if not _PATH_SEGMENT_RE.match(cleaned):
        raise ValueError(f"{field_name} has invalid format")
    return cleaned


def normalize_case_id(case_id: Optional[str]) -> str:
    cleaned = _clean_text(case_id)
    if cleaned is None:
        return UNKNOWN_CASE_ID
    normalized = "".join(cleaned.split()).upper()
    normalized = _CASE_ID_INVALID_RE.sub("_", normalized)
    normalized = _CASE_ID_MULTI_UNDERSCORE_RE.sub("_", normalized)
    normalized = normalized.strip("._-")
    return normalized or UNKNOWN_CASE_ID


def build_file_id(source_type: str, container_path: Optional[str], virtual_path: str) -> str:
    source_value = _clean_text(source_type)
    if source_value is None:
        raise ValueError("source_type is required")
    virtual_value = _clean_text(virtual_path)
    if virtual_value is None:
        raise ValueError("virtual_path is required")
    basis = f"{source_value}|{container_path or ''}|{virtual_value}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def build_chunk_id(file_id: str, page_start: int, page_end: int) -> str:
    file_value = normalize_stable_id(file_id, "file_id")
    start = _coerce_int(page_start, "page_start")
    end = _coerce_int(page_end, "page_end")
    if start < 0 or end < 0 or end < start:
        raise ValueError("page range must satisfy 0 <= page_start <= page_end")
    return f"{file_value}:{start}-{end}"


def build_artifact_id(
    artifact_type: str,
    scope_type: str,
    scope_id: str,
    version: str = "v1",
) -> str:
    artifact_value = normalize_stable_id(artifact_type, "artifact_type")
    scope_type_value = normalize_stable_id(scope_type, "scope_type")
    scope_id_value = normalize_stable_id(scope_id, "scope_id")
    version_value = normalize_stable_id(version, "version")
    basis = "|".join((artifact_value, scope_type_value, scope_id_value, version_value))
    return f"art_{hashlib.sha256(basis.encode('utf-8')).hexdigest()}"


def build_product_path(scope_type: str, scope_id: Optional[str], case_id_norm: Optional[str] = None) -> str:
    scope_value = normalize_stable_id(scope_type, "scope_type").lower()
    case_key = normalize_case_id(case_id_norm)
    if scope_value == "case":
        return f"case/{case_key}"

    scope_map = {
        "doc": "doc",
        "chunk": "chunk",
        "person": "person",
        "event": "event",
        "thread": "thread",
        "artifact": "artifact",
    }
    path_segment = scope_map.get(scope_value)
    if path_segment is None:
        raise ValueError(f"unsupported scope_type: {scope_type}")
    identifier = normalize_stable_id(scope_id or "", "scope_id")
    return f"case/{case_key}/{path_segment}/{identifier}"


@dataclass(frozen=True)
class EvidenceRef:
    file_id: str
    chunk_id: str
    page_start: int
    page_end: int
    source_phase: str
    extractor_version: str
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    quote: Optional[str] = None
    model: Optional[str] = None
    prompt_hash: Optional[str] = None
    confidence: Optional[float] = None

    def __post_init__(self):
        file_id = normalize_stable_id(self.file_id, "file_id")
        chunk_id = normalize_stable_id(self.chunk_id, "chunk_id")
        page_start = _coerce_int(self.page_start, "page_start")
        page_end = _coerce_int(self.page_end, "page_end")
        if page_start < 0 or page_end < 0 or page_end < page_start:
            raise ValueError("page range must satisfy 0 <= page_start <= page_end")

        source_phase = _clean_text(self.source_phase)
        if source_phase is None:
            raise ValueError("source_phase is required")
        extractor_version = _clean_text(self.extractor_version)
        if extractor_version is None:
            raise ValueError("extractor_version is required")

        char_start = self.char_start
        char_end = self.char_end
        if char_start is not None:
            char_start = _coerce_int(char_start, "char_start")
            if char_start < 0:
                raise ValueError("char_start must be >= 0")
        if char_end is not None:
            char_end = _coerce_int(char_end, "char_end")
            if char_end < 0:
                raise ValueError("char_end must be >= 0")
        if (char_start is None) != (char_end is None):
            raise ValueError("char_start and char_end must be provided together")
        if char_start is not None and char_end is not None and char_end < char_start:
            raise ValueError("char range must satisfy char_start <= char_end")

        quote = _clean_optional_text(self.quote)
        if quote is not None and len(quote) > MAX_QUOTE_LENGTH:
            raise ValueError(f"quote must be <= {MAX_QUOTE_LENGTH} characters")

        model = _clean_optional_text(self.model)
        prompt_hash = _clean_optional_text(self.prompt_hash)
        if prompt_hash is not None and not _PROMPT_HASH_RE.match(prompt_hash):
            raise ValueError("prompt_hash must be a lowercase hex hash")

        confidence = _coerce_optional_float(self.confidence, "confidence")
        if confidence is not None and not (0.0 <= confidence <= 1.0):
            raise ValueError("confidence must be in range [0, 1]")

        object.__setattr__(self, "file_id", file_id)
        object.__setattr__(self, "chunk_id", chunk_id)
        object.__setattr__(self, "page_start", page_start)
        object.__setattr__(self, "page_end", page_end)
        object.__setattr__(self, "source_phase", source_phase)
        object.__setattr__(self, "extractor_version", extractor_version)
        object.__setattr__(self, "char_start", char_start)
        object.__setattr__(self, "char_end", char_end)
        object.__setattr__(self, "quote", quote)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "prompt_hash", prompt_hash)
        object.__setattr__(self, "confidence", confidence)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "EvidenceRef":
        if not isinstance(payload, Mapping):
            raise ValueError("payload must be a mapping")
        return cls(
            file_id=payload.get("file_id"),
            chunk_id=payload.get("chunk_id"),
            page_start=payload.get("page_start"),
            page_end=payload.get("page_end"),
            source_phase=payload.get("source_phase"),
            extractor_version=payload.get("extractor_version"),
            char_start=payload.get("char_start"),
            char_end=payload.get("char_end"),
            quote=payload.get("quote"),
            model=payload.get("model"),
            prompt_hash=payload.get("prompt_hash"),
            confidence=payload.get("confidence"),
        )

    def to_dict(self) -> dict[str, object]:
        record: dict[str, object] = {
            "file_id": self.file_id,
            "chunk_id": self.chunk_id,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "source_phase": self.source_phase,
            "extractor_version": self.extractor_version,
        }
        if self.char_start is not None:
            record["char_start"] = self.char_start
        if self.char_end is not None:
            record["char_end"] = self.char_end
        if self.quote is not None:
            record["quote"] = self.quote
        if self.model is not None:
            record["model"] = self.model
        if self.prompt_hash is not None:
            record["prompt_hash"] = self.prompt_hash
        if self.confidence is not None:
            record["confidence"] = self.confidence
        return record

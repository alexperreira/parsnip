import gzip
from pathlib import Path

try:
    import zstandard as _zstandard
except ImportError:  # pragma: no cover
    _zstandard = None


MISSING_ZSTANDARD_ERROR = (
    "zstandard is required for '.zst' shards. Install it with: pip install zstandard"
)


def ensure_zstandard_available():
    if _zstandard is None:
        raise SystemExit(MISSING_ZSTANDARD_ERROR)


def shard_compression_from_name(shard_name):
    if shard_name.endswith(".jsonl.zst"):
        return "zstd"
    if shard_name.endswith(".jsonl.gz"):
        return "gzip"
    if shard_name.endswith(".jsonl"):
        return "none"
    return None


def shard_suffix_for_compression(compression):
    if compression == "zstd":
        return ".jsonl.zst"
    if compression == "gzip":
        return ".jsonl.gz"
    if compression == "none":
        return ".jsonl"
    raise ValueError(f"Unsupported compression: {compression}")


def open_text_reader(path):
    path = Path(path)
    if path.suffix == ".zst":
        ensure_zstandard_available()
        return _zstandard.open(path, "rt", encoding="utf-8")
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def open_text_writer(path, zstd_level=None):
    path = Path(path)
    if path.suffix == ".zst":
        ensure_zstandard_available()
        kwargs = {"encoding": "utf-8"}
        if zstd_level is not None:
            kwargs["cctx"] = _zstandard.ZstdCompressor(level=int(zstd_level))
        return _zstandard.open(path, "wt", **kwargs)
    if path.suffix == ".gz":
        return gzip.open(path, "wt", encoding="utf-8")
    return path.open("w", encoding="utf-8")

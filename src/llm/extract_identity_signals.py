import argparse
import json
import time
import urllib.error
from pathlib import Path

from llm.provider_client import (
    DEFAULT_GEMINI_BASE_URL,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OPENAI_BASE_URL,
    LLMProviderConfigError,
    call_llm,
)
from llm.cache import chunk_text_hash, connect_cache, default_cache_db_path, get_cached, put_cached


ALLOWED_ATTRIBUTES = {"dob", "address", "case_id"}


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Extract identity signals (DOB/address/case_id) from chunks.jsonl."
    )
    parser.add_argument(
        "--input",
        default="chunks.jsonl",
        help="Input chunks JSONL path (default: chunks.jsonl).",
    )
    parser.add_argument(
        "--output",
        default="identity_signals.jsonl",
        help="Output JSONL path (default: identity_signals.jsonl).",
    )
    parser.add_argument(
        "--signals",
        action="store_true",
        help="Use fast signals model defaults (llama3.1:8b).",
    )
    parser.add_argument(
        "--narrative",
        action="store_true",
        help="Use narrative model defaults (qwen2.5:32b).",
    )
    parser.add_argument(
        "--provider",
        choices=("ollama", "openai", "gemini"),
        default="ollama",
        help="LLM provider (default: ollama).",
    )
    parser.add_argument("--model", default="llama3", help="LLM model name.")
    parser.add_argument(
        "--host",
        default=DEFAULT_OLLAMA_HOST,
        help=f"Ollama host for --provider=ollama (default: {DEFAULT_OLLAMA_HOST}).",
    )
    parser.add_argument(
        "--openai-base-url",
        default=DEFAULT_OPENAI_BASE_URL,
        help=f"OpenAI API base URL for --provider=openai (default: {DEFAULT_OPENAI_BASE_URL}).",
    )
    parser.add_argument(
        "--gemini-base-url",
        default=DEFAULT_GEMINI_BASE_URL,
        help=f"Gemini API base URL for --provider=gemini (default: {DEFAULT_GEMINI_BASE_URL}).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Request timeout in seconds (default: 120).",
    )
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=None,
        help="Optional limit on chunks processed.",
    )
    parser.add_argument(
        "--cache",
        action="store_true",
        help="Enable per-chunk caching to a local sqlite DB (off by default).",
    )
    parser.add_argument(
        "--cache-db",
        default=None,
        help="Optional sqlite cache DB path (defaults to <output>.cache.sqlite when --cache is set).",
    )
    parser.add_argument(
        "--cache-retry-errors",
        action="store_true",
        help="When caching is enabled, retry cached error records instead of reusing them.",
    )
    return parser.parse_args()


def _resolve_model(args):
    if args.signals and args.narrative:
        raise SystemExit("Choose only one of --signals or --narrative.")
    if args.provider == "openai":
        if args.signals or args.narrative:
            raise SystemExit("--signals/--narrative are only supported with --provider=ollama.")
        if args.model == "llama3":
            raise SystemExit("When --provider=openai, pass --model explicitly.")
        return args.model
    if args.provider == "gemini":
        if args.signals or args.narrative:
            raise SystemExit("--signals/--narrative are only supported with --provider=ollama.")
        if args.model == "llama3":
            return "gemini-3.1-pro"
        return args.model
    if args.model != "llama3":
        return args.model
    if args.signals:
        return "llama3.1:8b"
    if args.narrative:
        return "qwen2.5:32b"
    return args.model


def _build_prompt(text):
    return (
        "You are an information extraction engine. "
        "Return ONLY valid JSON with this schema:\n"
        '{ "items": [ { "person": string, "attribute": string, "value": string, '
        '"quote": string, "confidence": float } ] }\n'
        "Use an empty list if no identity signals are found. "
        "Only emit attribute values from this set: dob, address, case_id. "
        "If you emit dob, prefer ISO-8601 (YYYY-MM-DD) when possible. "
        "Quotes must be short, verbatim spans from the input.\n\n"
        f"INPUT:\n{text}"
    )


def _parse_response(text):
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, "invalid_json"
    items = payload.get("items")
    if not isinstance(items, list):
        return None, "missing_items"
    return _clean_items(items), None


def _clean_items(items):
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        person = item.get("person")
        attribute = item.get("attribute")
        value = item.get("value")
        quote = item.get("quote")
        confidence = item.get("confidence")
        if not isinstance(person, str) or not person.strip():
            continue
        if not isinstance(attribute, str) or not attribute.strip():
            continue
        attribute = attribute.strip().lower()
        if attribute not in ALLOWED_ATTRIBUTES:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        if not isinstance(quote, str) or not quote.strip():
            continue
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            continue
        cleaned.append(
            {
                "person": person.strip(),
                "attribute": attribute,
                "value": value.strip(),
                "quote": quote.strip(),
                "confidence": float(confidence),
            }
        )
    return cleaned


def main():
    args = _parse_args()
    model = _resolve_model(args)
    processed = 0
    errors = 0
    started = time.monotonic()
    extractor_version = f"identity-signals:v1:{args.provider}:{model}"

    cache_conn = None
    if args.cache:
        db_path = Path(args.cache_db) if args.cache_db else default_cache_db_path(args.output)
        cache_conn = connect_cache(db_path)

    with open(args.input, "r", encoding="utf-8") as in_handle, open(
        args.output, "w", encoding="utf-8"
    ) as out_handle:
        for line in in_handle:
            if args.max_chunks is not None and processed >= args.max_chunks:
                break
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                errors += 1
                continue

            chunk_text = record.get("text", "")
            chunk_id = record.get("chunk_id")
            if cache_conn is not None and isinstance(chunk_id, str) and chunk_id:
                text_hash = chunk_text_hash(chunk_text)
                cached = get_cached(
                    cache_conn,
                    extractor_version=extractor_version,
                    chunk_id=chunk_id,
                    chunk_text_hash_value=text_hash,
                )
                if cached.hit and cached.output_record is not None:
                    cached_error = cached.output_record.get("error")
                    if not (args.cache_retry_errors and cached_error):
                        out_handle.write(json.dumps(cached.output_record, ensure_ascii=True) + "\n")
                        processed += 1
                        if cached_error:
                            errors += 1
                        continue

            prompt = _build_prompt(chunk_text)
            error = None
            items = []
            try:
                response_text = call_llm(
                    prompt,
                    model,
                    args.provider,
                    args.host,
                    args.timeout,
                    args.openai_base_url,
                    args.gemini_base_url,
                )
                items, error = _parse_response(response_text)
                if error:
                    errors += 1
                    items = []
            except LLMProviderConfigError:
                errors += 1
                error = f"{args.provider}_config_error"
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
                errors += 1
                error = f"{args.provider}_unavailable"
            except Exception:
                errors += 1
                error = f"{args.provider}_error"

            output_record = {
                "file_id": record.get("file_id"),
                "chunk_id": chunk_id,
                "page_range": [record.get("page_start"), record.get("page_end")],
                "items": items,
                "model": model,
                "error": error,
            }
            out_handle.write(json.dumps(output_record, ensure_ascii=True) + "\n")
            if cache_conn is not None and isinstance(chunk_id, str) and chunk_id:
                put_cached(
                    cache_conn,
                    extractor_version=extractor_version,
                    chunk_id=chunk_id,
                    chunk_text_hash_value=chunk_text_hash(chunk_text),
                    output_record=output_record,
                )
                if processed % 1000 == 0:
                    cache_conn.commit()
            processed += 1
    if cache_conn is not None:
        cache_conn.commit()
        cache_conn.close()

    elapsed = round(time.monotonic() - started, 3)
    print("LLM identity signals summary")
    print(f"  processed: {processed}")
    print(f"  errors: {errors}")
    print(f"  elapsed_seconds: {elapsed}")


if __name__ == "__main__":
    main()

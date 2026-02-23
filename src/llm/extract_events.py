import argparse
import json
import time
import urllib.error

from llm.provider_client import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OPENAI_BASE_URL,
    LLMProviderConfigError,
    call_llm,
)


def _parse_args():
    parser = argparse.ArgumentParser(description="Extract events from chunks.jsonl.")
    parser.add_argument(
        "--input",
        default="chunks.jsonl",
        help="Input chunks JSONL path (default: chunks.jsonl).",
    )
    parser.add_argument(
        "--output",
        default="events.jsonl",
        help="Output JSONL path (default: events.jsonl).",
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
        choices=("ollama", "openai"),
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
        '{ "items": [ { "event": string, "date": string, "quote": string, "confidence": float } ] }\n'
        "Use an empty list if no events are found. "
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
    return items, None


def main():
    args = _parse_args()
    model = _resolve_model(args)
    processed = 0
    errors = 0
    started = time.monotonic()

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
                "chunk_id": record.get("chunk_id"),
                "page_range": [record.get("page_start"), record.get("page_end")],
                "items": items,
                "model": model,
                "error": error,
            }
            out_handle.write(json.dumps(output_record, ensure_ascii=True) + "\n")
            processed += 1

    elapsed = round(time.monotonic() - started, 3)
    print("LLM events summary")
    print(f"  processed: {processed}")
    print(f"  errors: {errors}")
    print(f"  elapsed_seconds: {elapsed}")


if __name__ == "__main__":
    main()

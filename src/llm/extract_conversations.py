import argparse
import json
import time
import urllib.error
import urllib.request


def _parse_args():
    parser = argparse.ArgumentParser(description="Extract conversations from chunks.jsonl.")
    parser.add_argument(
        "--input",
        default="chunks.jsonl",
        help="Input chunks JSONL path (default: chunks.jsonl).",
    )
    parser.add_argument(
        "--output",
        default="conversations.jsonl",
        help="Output JSONL path (default: conversations.jsonl).",
    )
    parser.add_argument("--model", default="llama3", help="Ollama model name.")
    parser.add_argument(
        "--host",
        default="http://localhost:11434",
        help="Ollama host (default: http://localhost:11434).",
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


def _build_prompt(text):
    return (
        "You are an information extraction engine. "
        "Return ONLY valid JSON with this schema:\n"
        '{ "items": [ { "speaker": string, "quote": string, "confidence": float } ] }\n'
        "Use an empty list if no conversations are found. "
        "Quotes must be short, verbatim spans from the input.\n\n"
        f"INPUT:\n{text}"
    )


def _call_ollama(prompt, model, host, timeout):
    url = host.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    parsed = json.loads(body)
    return parsed.get("response", "")


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
                response_text = _call_ollama(prompt, args.model, args.host, args.timeout)
                items, error = _parse_response(response_text)
                if error:
                    errors += 1
                    items = []
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
                errors += 1
                error = "ollama_unavailable"
            except Exception:
                errors += 1
                error = "ollama_error"

            output_record = {
                "file_id": record.get("file_id"),
                "chunk_id": record.get("chunk_id"),
                "page_range": [record.get("page_start"), record.get("page_end")],
                "items": items,
                "model": args.model,
                "error": error,
            }
            out_handle.write(json.dumps(output_record, ensure_ascii=True) + "\n")
            processed += 1

    elapsed = round(time.monotonic() - started, 3)
    print("LLM conversations summary")
    print(f"  processed: {processed}")
    print(f"  errors: {errors}")
    print(f"  elapsed_seconds: {elapsed}")


if __name__ == "__main__":
    main()

import argparse
import json
import time


def _parse_args():
    parser = argparse.ArgumentParser(description="Phase 1 JSONL summary.")
    parser.add_argument("--input", required=True, help="Phase 1 JSONL path.")
    return parser.parse_args()


def summarize_phase1(input_path):
    counts_by_class = {"text": 0, "scanned": 0, "mixed": 0, "unknown": 0}
    total = 0
    sampled = 0
    errors = 0
    text_char_total = 0
    started = time.monotonic()

    with open(input_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            classification = record.get("classification", "unknown")
            if classification not in counts_by_class:
                classification = "unknown"
            counts_by_class[classification] += 1
            if record.get("sampled"):
                sampled += 1
            if record.get("errors"):
                errors += 1
            text_char_total += int(record.get("text_char_count_total") or 0)

    elapsed = time.monotonic() - started
    avg_text_chars = (text_char_total / total) if total else 0
    summary = {
        "total": total,
        "sampled": sampled,
        "errors": errors,
        "counts_by_class": counts_by_class,
        "avg_text_char_count": round(avg_text_chars, 2),
        "elapsed_seconds": round(elapsed, 3),
    }
    return summary


def _print_summary(summary):
    print("Phase 1 report")
    print(f"  total: {summary['total']}")
    print(f"  sampled: {summary['sampled']}")
    print(f"  errors: {summary['errors']}")
    print(f"  class text: {summary['counts_by_class']['text']}")
    print(f"  class scanned: {summary['counts_by_class']['scanned']}")
    print(f"  class mixed: {summary['counts_by_class']['mixed']}")
    print(f"  class unknown: {summary['counts_by_class']['unknown']}")
    print(f"  avg_text_char_count: {summary['avg_text_char_count']}")
    print(f"  elapsed_seconds: {summary['elapsed_seconds']}")


def main():
    args = _parse_args()
    summary = summarize_phase1(args.input)
    _print_summary(summary)


if __name__ == "__main__":
    main()

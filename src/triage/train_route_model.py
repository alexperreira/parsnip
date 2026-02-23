import argparse
import hashlib
import json
import math
import pickle
import random
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from file_parser.compress_io import open_text_reader, open_text_writer

OFFICIAL_ROUTE_LABELS: Tuple[str, ...] = ("skip", "llm_small", "llm_large")
SEND_TO_LLM_LABELS = {"llm_small", "llm_large"}
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True)
class RouteRow:
    file_id: str
    chunk_id: str
    text: str
    label_route: str
    triage_route: Optional[str]
    token_est: int


def _parse_args():
    parser = argparse.ArgumentParser(description="Train a baseline route classifier.")
    parser.add_argument(
        "--input",
        default="output/ml/route_dataset.jsonl",
        help="Route dataset JSONL path (default: output/ml/route_dataset.jsonl).",
    )
    parser.add_argument(
        "--output-model",
        default="output/ml/route_model.pkl",
        help="Pickle output path for model artifact (default: output/ml/route_model.pkl).",
    )
    parser.add_argument(
        "--output-report",
        default="output/ml/route_eval.json",
        help="Evaluation report JSON path (default: output/ml/route_eval.json).",
    )
    parser.add_argument(
        "--output-version",
        default="output/ml/model_version.json",
        help="Model version metadata JSON path (default: output/ml/model_version.json).",
    )
    parser.add_argument(
        "--split-by",
        default="file_id",
        choices=["file_id"],
        help="Group key for deterministic train/test split (default: file_id).",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=17,
        help="Random seed for deterministic splitting (default: 17).",
    )
    parser.add_argument(
        "--test-fraction",
        type=float,
        default=0.2,
        help="Fraction of split groups sent to test set (default: 0.2).",
    )
    parser.add_argument(
        "--max-features",
        type=int,
        default=5000,
        help="Maximum vocabulary size from training text (default: 5000).",
    )
    parser.add_argument(
        "--min-df",
        type=int,
        default=1,
        help="Minimum document frequency for tokens to enter vocabulary (default: 1).",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=1.0,
        help="Laplace smoothing value for Naive Bayes (default: 1.0).",
    )
    return parser.parse_args()


def _iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    with open_text_reader(path) as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _tokenize(text: str) -> List[str]:
    if not isinstance(text, str):
        return []
    return [token.lower() for token in TOKEN_RE.findall(text)]


def split_file_ids(file_ids: Sequence[str], *, random_seed: int, test_fraction: float) -> Tuple[List[str], List[str]]:
    unique_ids = sorted({file_id for file_id in file_ids if isinstance(file_id, str) and file_id})
    if not unique_ids:
        return [], []
    if len(unique_ids) == 1:
        return unique_ids, []

    shuffled = list(unique_ids)
    random.Random(random_seed).shuffle(shuffled)
    raw_test_count = int(round(len(shuffled) * test_fraction))
    test_count = max(1, raw_test_count)
    test_count = min(test_count, len(shuffled) - 1)
    test_ids = sorted(shuffled[:test_count])
    train_ids = sorted(shuffled[test_count:])
    return train_ids, test_ids


def _load_rows(path: Path) -> Tuple[List[RouteRow], Dict[str, int]]:
    rows: List[RouteRow] = []
    stats = {"rows_total": 0, "rows_kept": 0, "rows_skipped": 0, "invalid_labels": 0}
    for record in _iter_jsonl(path):
        stats["rows_total"] += 1
        file_id = record.get("file_id")
        chunk_id = record.get("chunk_id")
        if not isinstance(file_id, str) or not file_id or not isinstance(chunk_id, str) or not chunk_id:
            stats["rows_skipped"] += 1
            continue

        text = record.get("text")
        text_val = text if isinstance(text, str) else ""

        labels = record.get("labels") if isinstance(record.get("labels"), dict) else {}
        label_route = labels.get("label_route")
        if not isinstance(label_route, str):
            label_route = record.get("label_route")
        if label_route not in OFFICIAL_ROUTE_LABELS:
            stats["invalid_labels"] += 1
            stats["rows_skipped"] += 1
            continue

        triage = record.get("triage") if isinstance(record.get("triage"), dict) else {}
        triage_route = triage.get("route")
        if triage_route is not None and not isinstance(triage_route, str):
            triage_route = str(triage_route)
        token_est = _as_int(triage.get("token_est"))
        if token_est <= 0:
            token_est = max(1, len(_tokenize(text_val)))

        rows.append(
            RouteRow(
                file_id=file_id,
                chunk_id=chunk_id,
                text=text_val,
                label_route=label_route,
                triage_route=triage_route,
                token_est=token_est,
            )
        )
        stats["rows_kept"] += 1
    return rows, stats


def _build_vocabulary(rows: Iterable[RouteRow], *, max_features: int, min_df: int) -> Dict[str, int]:
    tf: Counter = Counter()
    df: Counter = Counter()
    for row in rows:
        tokens = _tokenize(row.text)
        tf.update(tokens)
        df.update(set(tokens))

    candidates = [token for token, doc_freq in df.items() if doc_freq >= min_df]
    candidates.sort(key=lambda token: (-tf[token], token))
    if max_features > 0:
        candidates = candidates[:max_features]
    return {token: idx for idx, token in enumerate(candidates)}


def _dataset_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _train_multinomial_nb(
    train_rows: Sequence[RouteRow], *, vocab: Dict[str, int], alpha: float
) -> Dict[str, Any]:
    class_doc_counts: Counter = Counter()
    class_token_counts: Dict[str, Counter] = {label: Counter() for label in OFFICIAL_ROUTE_LABELS}
    class_total_tokens: Counter = Counter()

    for row in train_rows:
        class_doc_counts[row.label_route] += 1
        counts = Counter(token for token in _tokenize(row.text) if token in vocab)
        class_token_counts[row.label_route].update(counts)
        class_total_tokens[row.label_route] += sum(counts.values())

    total_docs = max(1, len(train_rows))
    class_count = len(OFFICIAL_ROUTE_LABELS)
    vocab_size = max(1, len(vocab))

    log_priors: Dict[str, float] = {}
    log_unknown_probs: Dict[str, float] = {}
    log_token_probs: Dict[str, List[float]] = {}
    for label in OFFICIAL_ROUTE_LABELS:
        prior = (class_doc_counts[label] + alpha) / (total_docs + (alpha * class_count))
        log_priors[label] = math.log(prior)

        denom = class_total_tokens[label] + (alpha * vocab_size)
        log_unknown_probs[label] = math.log(alpha / denom)
        probs = [0.0] * vocab_size
        token_counts = class_token_counts[label]
        for token, index in vocab.items():
            probs[index] = math.log((token_counts.get(token, 0) + alpha) / denom)
        log_token_probs[label] = probs

    return {
        "model_type": "multinomial_nb",
        "classes": list(OFFICIAL_ROUTE_LABELS),
        "vocab": vocab,
        "log_priors": log_priors,
        "log_unknown_probs": log_unknown_probs,
        "log_token_probs": log_token_probs,
        "alpha": alpha,
        "token_regex": TOKEN_RE.pattern,
    }


def _predict_proba(model: Dict[str, Any], text: str) -> Dict[str, float]:
    vocab = model["vocab"]
    tokens = _tokenize(text)
    token_counts = Counter(token for token in tokens if token in vocab)
    known_total = sum(token_counts.values())
    unknown_total = max(0, len(tokens) - known_total)

    log_scores: Dict[str, float] = {}
    for label in OFFICIAL_ROUTE_LABELS:
        score = model["log_priors"][label]
        token_probs = model["log_token_probs"][label]
        for token, count in token_counts.items():
            score += count * token_probs[vocab[token]]
        if unknown_total:
            score += unknown_total * model["log_unknown_probs"][label]
        log_scores[label] = score

    max_log = max(log_scores.values())
    exp_scores = {label: math.exp(score - max_log) for label, score in log_scores.items()}
    total = sum(exp_scores.values()) or 1.0
    return {label: exp_scores[label] / total for label in OFFICIAL_ROUTE_LABELS}


def load_route_model(path: Path) -> Dict[str, Any]:
    with path.open("rb") as handle:
        model = pickle.load(handle)
    if not isinstance(model, dict):
        raise ValueError("Route model artifact must be a dict.")
    classes = model.get("classes")
    if classes != list(OFFICIAL_ROUTE_LABELS):
        raise ValueError("Route model classes are invalid or unsupported.")
    vocab = model.get("vocab")
    if not isinstance(vocab, dict):
        raise ValueError("Route model vocabulary is missing.")
    required_maps = ("log_priors", "log_unknown_probs", "log_token_probs")
    for key in required_maps:
        if not isinstance(model.get(key), dict):
            raise ValueError(f"Route model field {key!r} is missing.")
    for label in OFFICIAL_ROUTE_LABELS:
        if label not in model["log_priors"]:
            raise ValueError("Route model priors are incomplete.")
        if label not in model["log_unknown_probs"]:
            raise ValueError("Route model unknown-token probabilities are incomplete.")
        token_probs = model["log_token_probs"].get(label)
        if not isinstance(token_probs, list):
            raise ValueError("Route model token probabilities are incomplete.")
        if len(token_probs) != len(vocab):
            raise ValueError("Route model token probability dimensions do not match vocabulary size.")
    return model


def predict_route_probabilities(model: Dict[str, Any], text: str) -> Dict[str, float]:
    return _predict_proba(model, text)


def _predict_label(probabilities: Dict[str, float]) -> str:
    best = OFFICIAL_ROUTE_LABELS[0]
    best_prob = probabilities.get(best, 0.0)
    for label in OFFICIAL_ROUTE_LABELS[1:]:
        score = probabilities.get(label, 0.0)
        if score > best_prob:
            best = label
            best_prob = score
    return best


def _init_confusion() -> Dict[str, Dict[str, int]]:
    return {
        actual: {predicted: 0 for predicted in OFFICIAL_ROUTE_LABELS}
        for actual in OFFICIAL_ROUTE_LABELS
    }


def _per_class_metrics(confusion: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, float]]:
    metrics: Dict[str, Dict[str, float]] = {}
    for label in OFFICIAL_ROUTE_LABELS:
        tp = confusion[label][label]
        fp = sum(confusion[actual][label] for actual in OFFICIAL_ROUTE_LABELS if actual != label)
        fn = sum(confusion[label][pred] for pred in OFFICIAL_ROUTE_LABELS if pred != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        metrics[label] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
    return metrics


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _evaluate(
    model: Dict[str, Any], rows: Sequence[RouteRow], *, split_name: str
) -> Dict[str, Any]:
    confusion = _init_confusion()
    total = 0
    correct = 0
    predicted_class_counts = {label: 0 for label in OFFICIAL_ROUTE_LABELS}
    predicted_llm_chunks = 0
    predicted_llm_tokens_est = 0
    positive_actual = 0
    positive_predicted = 0
    positive_baseline_predicted = 0

    for row in rows:
        probs = _predict_proba(model, row.text)
        predicted = _predict_label(probs)
        actual = row.label_route
        confusion[actual][predicted] += 1
        total += 1
        predicted_class_counts[predicted] += 1
        if predicted == actual:
            correct += 1

        actual_positive = actual in SEND_TO_LLM_LABELS
        predicted_positive = predicted in SEND_TO_LLM_LABELS
        baseline_positive = row.triage_route in SEND_TO_LLM_LABELS
        if actual_positive:
            positive_actual += 1
            if predicted_positive:
                positive_predicted += 1
            if baseline_positive:
                positive_baseline_predicted += 1
        if predicted_positive:
            predicted_llm_chunks += 1
            predicted_llm_tokens_est += row.token_est

    per_class = _per_class_metrics(confusion)
    macro_f1 = sum(per_class[label]["f1"] for label in OFFICIAL_ROUTE_LABELS) / len(OFFICIAL_ROUTE_LABELS)

    return {
        "split_name": split_name,
        "accuracy": _safe_ratio(correct, total),
        "macro_f1": macro_f1,
        "confusion_matrix": confusion,
        "per_class": per_class,
        "llm_send_recall": {
            "model": _safe_ratio(positive_predicted, positive_actual),
            "baseline_heuristic": _safe_ratio(positive_baseline_predicted, positive_actual),
            "support": positive_actual,
        },
        "llm_workload": {
            "predicted_llm_chunks": predicted_llm_chunks,
            "predicted_llm_ratio": _safe_ratio(predicted_llm_chunks, total),
            "predicted_llm_tokens_est": predicted_llm_tokens_est,
            "predicted_class_counts": predicted_class_counts,
        },
    }


def train_route_model(
    *,
    input_path: Path,
    output_model_path: Path,
    output_report_path: Path,
    output_version_path: Path,
    split_by: str = "file_id",
    random_seed: int = 17,
    test_fraction: float = 0.2,
    max_features: int = 5000,
    min_df: int = 1,
    alpha: float = 1.0,
) -> Dict[str, Any]:
    if split_by != "file_id":
        raise SystemExit(f"Unsupported split-by value: {split_by}")
    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")
    if not (0.0 < test_fraction < 1.0):
        raise SystemExit("--test-fraction must be between 0 and 1.")
    if alpha <= 0.0:
        raise SystemExit("--alpha must be > 0.")
    if min_df < 1:
        raise SystemExit("--min-df must be >= 1.")

    rows, data_stats = _load_rows(input_path)
    if not rows:
        raise SystemExit("No usable rows found in dataset.")

    train_file_ids, test_file_ids = split_file_ids(
        [row.file_id for row in rows], random_seed=random_seed, test_fraction=test_fraction
    )
    if not train_file_ids:
        raise SystemExit("Unable to create a train split.")

    train_set = set(train_file_ids)
    test_set = set(test_file_ids)
    train_rows = [row for row in rows if row.file_id in train_set]
    test_rows = [row for row in rows if row.file_id in test_set]
    if not test_rows:
        raise SystemExit("Test split is empty; provide at least 2 distinct file_id values.")

    vocab = _build_vocabulary(train_rows, max_features=max_features, min_df=min_df)
    model = _train_multinomial_nb(train_rows, vocab=vocab, alpha=alpha)
    eval_metrics = _evaluate(model, test_rows, split_name="test")

    dataset_sha256 = _dataset_hash(input_path)
    output_model_path.parent.mkdir(parents=True, exist_ok=True)
    output_report_path.parent.mkdir(parents=True, exist_ok=True)
    output_version_path.parent.mkdir(parents=True, exist_ok=True)

    with output_model_path.open("wb") as handle:
        pickle.dump(model, handle, protocol=pickle.HIGHEST_PROTOCOL)

    report = {
        "schema_version": 1,
        "dataset": {
            "input_path": input_path.as_posix(),
            "dataset_sha256": dataset_sha256,
            **data_stats,
        },
        "split": {
            "strategy": split_by,
            "random_seed": random_seed,
            "test_fraction": test_fraction,
            "train_file_ids": train_file_ids,
            "test_file_ids": test_file_ids,
            "rows_train": len(train_rows),
            "rows_test": len(test_rows),
        },
        "classes": list(OFFICIAL_ROUTE_LABELS),
        "metrics": eval_metrics,
        "model": {
            "model_type": model["model_type"],
            "vocabulary_size": len(model["vocab"]),
            "alpha": alpha,
            "min_df": min_df,
            "max_features": max_features,
        },
    }
    with open_text_writer(output_report_path) as handle:
        handle.write(json.dumps(report, ensure_ascii=True, indent=2) + "\n")

    model_version = {
        "schema_version": 1,
        "dataset_sha256": dataset_sha256,
        "class_set": list(OFFICIAL_ROUTE_LABELS),
        "split_by": split_by,
        "random_seed": random_seed,
        "model_type": model["model_type"],
        "feature_config": {
            "token_regex": TOKEN_RE.pattern,
            "max_features": max_features,
            "min_df": min_df,
            "alpha": alpha,
        },
        "artifact_paths": {
            "model": output_model_path.as_posix(),
            "report": output_report_path.as_posix(),
        },
    }
    with open_text_writer(output_version_path) as handle:
        handle.write(json.dumps(model_version, ensure_ascii=True, indent=2) + "\n")

    return {
        "dataset_sha256": dataset_sha256,
        "rows_train": len(train_rows),
        "rows_test": len(test_rows),
        "vocabulary_size": len(vocab),
        "model_path": output_model_path.as_posix(),
        "report_path": output_report_path.as_posix(),
        "version_path": output_version_path.as_posix(),
    }


def main():
    args = _parse_args()
    summary = train_route_model(
        input_path=Path(args.input),
        output_model_path=Path(args.output_model),
        output_report_path=Path(args.output_report),
        output_version_path=Path(args.output_version),
        split_by=args.split_by,
        random_seed=int(args.random_seed),
        test_fraction=float(args.test_fraction),
        max_features=int(args.max_features),
        min_df=int(args.min_df),
        alpha=float(args.alpha),
    )
    print("Route model training summary")
    for key in sorted(summary.keys()):
        print(f"- {key}: {summary[key]}")


if __name__ == "__main__":
    main()

import json
import sys
import time
from pathlib import Path

import typer

from file_parser.manifest_builder import main as manifest_main
from file_parser.phase1_detect import main as phase1_main
from file_parser.phase1_report import main as report_main
from file_parser.phase2_ocr import main as phase2_main
from file_parser.phase7_validate import main as phase7_main
from file_parser.run_pipeline import main as pipeline_main
from chunking.phase4_chunk import main as phase4_main
from entity_resolution.phase8_resolve_people import main as resolve_people_main
from llm.extract_conversations import main as llm_conversations_main
from llm.extract_entities import main as llm_entities_main
from llm.extract_events import main as llm_events_main
from llm.extract_identity_signals import main as llm_identity_signals_main
from llm.provider_client import DEFAULT_GEMINI_BASE_URL, DEFAULT_OLLAMA_HOST, DEFAULT_OPENAI_BASE_URL
from loaders.load_conversations import main as load_conversations_main
from loaders.load_entities import main as load_entities_main
from loaders.load_events import main as load_events_main
from loaders.load_identity_signals import main as load_identity_signals_main
from loaders.load_manifest import main as load_manifest_main
from text_extraction.phase3_extract_text import main as phase3_main
from timeline.phase9_stitch_timeline import main as timeline_main
from conversation_threading.phase10_thread_conversations import main as thread_main
from triage.phase_t1_triage_chunks import main as triage_main
from triage.build_route_dataset import main as route_dataset_main
from triage.sample_route_labels import main as route_sample_labels_main

app = typer.Typer(
    add_completion=False,
    help="File parsing pipeline CLI.",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)


def _dispatch_to_main(command: str, remainder, handler):
    prior_argv = sys.argv
    sys.argv = [f"fileparse {command}", *remainder]
    try:
        return handler()
    finally:
        sys.argv = prior_argv


llm_app = typer.Typer(
    add_completion=False,
    help="LLM extraction commands.",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
app.add_typer(llm_app, name="llm")

load_app = typer.Typer(
    add_completion=False,
    help="SQLite loader commands.",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
app.add_typer(load_app, name="load")


def _run_load_stage(
    entities_path: Path,
    events_path: Path,
    conversations_path: Path,
    db_path: Path,
    overwrite: bool = False,
):
    entity_args = ["--input", str(entities_path), "--db", str(db_path)]
    if overwrite:
        entity_args.append("--overwrite")
    _dispatch_to_main("load entities", entity_args, load_entities_main)
    _dispatch_to_main(
        "load events",
        ["--input", str(events_path), "--db", str(db_path)],
        load_events_main,
    )
    _dispatch_to_main(
        "load conversations",
        ["--input", str(conversations_path), "--db", str(db_path)],
        load_conversations_main,
    )


@app.command("pipeline", help="Run Phase 0-2 end-to-end.")
def pipeline(ctx: typer.Context):
    _dispatch_to_main("pipeline", list(ctx.args), pipeline_main)


@app.command("manifest", help="Build a PDF manifest (Phase 0).")
def manifest(ctx: typer.Context):
    _dispatch_to_main("manifest", list(ctx.args), manifest_main)


@app.command("phase1", help="Classify PDFs as text/scanned/mixed/unknown.")
def phase1(ctx: typer.Context):
    _dispatch_to_main("phase1", list(ctx.args), phase1_main)


@app.command("report", help="Summarize Phase 1 results.")
def report(ctx: typer.Context):
    _dispatch_to_main("report", list(ctx.args), report_main)


@app.command("phase2", help="Run OCR for scanned/mixed PDFs.")
def phase2(ctx: typer.Context):
    _dispatch_to_main("phase2", list(ctx.args), phase2_main)


@app.command("extract-text", help="Run unified text extraction (Phase 3).")
def extract_text(ctx: typer.Context):
    _dispatch_to_main("extract-text", list(ctx.args), phase3_main)


@llm_app.command("entities", help="Extract entities from chunks.jsonl via LLM.")
def llm_entities(ctx: typer.Context):
    _dispatch_to_main("llm entities", list(ctx.args), llm_entities_main)


@llm_app.command("events", help="Extract events from chunks.jsonl via LLM.")
def llm_events(ctx: typer.Context):
    _dispatch_to_main("llm events", list(ctx.args), llm_events_main)


@llm_app.command("conversations", help="Extract conversations from chunks.jsonl via LLM.")
def llm_conversations(ctx: typer.Context):
    _dispatch_to_main("llm conversations", list(ctx.args), llm_conversations_main)


@llm_app.command("identity-signals", help="Extract identity signals from chunks.jsonl via LLM.")
def llm_identity_signals(ctx: typer.Context):
    _dispatch_to_main("llm identity-signals", list(ctx.args), llm_identity_signals_main)


@load_app.command("entities", help="Load entities JSONL into SQLite.")
def load_entities(ctx: typer.Context):
    _dispatch_to_main("load entities", list(ctx.args), load_entities_main)


@load_app.command("events", help="Load events JSONL into SQLite.")
def load_events(ctx: typer.Context):
    _dispatch_to_main("load events", list(ctx.args), load_events_main)


@load_app.command("conversations", help="Load conversations JSONL into SQLite.")
def load_conversations(ctx: typer.Context):
    _dispatch_to_main("load conversations", list(ctx.args), load_conversations_main)


@load_app.command("identity-signals", help="Load identity signals JSONL into SQLite.")
def load_identity_signals(ctx: typer.Context):
    _dispatch_to_main("load identity-signals", list(ctx.args), load_identity_signals_main)


@load_app.command("manifest", help="Load Phase 0 manifest JSONL into SQLite.")
def load_manifest(ctx: typer.Context):
    _dispatch_to_main("load manifest", list(ctx.args), load_manifest_main)


@load_app.command("all", help="Load entities, events, and conversations into SQLite.")
def load_all(
    entities_input: str = typer.Option(
        "entities.jsonl",
        "--entities-input",
        help="Entities JSONL path (default: entities.jsonl).",
    ),
    events_input: str = typer.Option(
        "events.jsonl",
        "--events-input",
        help="Events JSONL path (default: events.jsonl).",
    ),
    conversations_input: str = typer.Option(
        "conversations.jsonl",
        "--conversations-input",
        help="Conversations JSONL path (default: conversations.jsonl).",
    ),
    db: str = typer.Option(
        "output/store.sqlite",
        "--db",
        help="SQLite DB path (default: output/store.sqlite).",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Drop and recreate all store tables before loading.",
    ),
):
    _run_load_stage(
        Path(entities_input),
        Path(events_input),
        Path(conversations_input),
        Path(db),
        overwrite=overwrite,
    )


@app.command("chunk", help="Run Phase 4 chunking for analysis.")
def chunk(ctx: typer.Context):
    _dispatch_to_main("chunk", list(ctx.args), phase4_main)


@app.command("triage", help="Run Phase T1 chunk triage (compute strategy routing).")
def triage(ctx: typer.Context):
    _dispatch_to_main("triage", list(ctx.args), triage_main)


@app.command("route-dataset", help="Build a route classification dataset from outputs.")
def route_dataset(ctx: typer.Context):
    _dispatch_to_main("route-dataset", list(ctx.args), route_dataset_main)


@app.command("route-train", help="Train a baseline route classifier from route_dataset.jsonl.")
def route_train(ctx: typer.Context):
    from triage.train_route_model import main as route_train_main

    _dispatch_to_main("route-train", list(ctx.args), route_train_main)


@app.command("route-sample-labels", help="Sample uncertain route-dataset rows for labeling.")
def route_sample_labels(ctx: typer.Context):
    _dispatch_to_main("route-sample-labels", list(ctx.args), route_sample_labels_main)


@app.command("validate", help="Run Phase 7 sanity checks.")
def validate(ctx: typer.Context):
    _dispatch_to_main("validate", list(ctx.args), phase7_main)


@app.command("resolve", help="Run people entity resolution (Phase 8).")
def resolve(ctx: typer.Context):
    _dispatch_to_main("resolve", list(ctx.args), resolve_people_main)


@app.command("timeline", help="Normalize event dates and stitch timelines (Phase 9).")
def timeline(ctx: typer.Context):
    _dispatch_to_main("timeline", list(ctx.args), timeline_main)


@app.command("thread", help="Thread conversations across documents (Phase 10).")
def thread(ctx: typer.Context):
    _dispatch_to_main("thread", list(ctx.args), thread_main)


@app.command("run", help="Run extract-text, chunk, llm, load, and validate steps.")
def run(
    ctx: typer.Context,
    input_dir: str = typer.Option(..., "--input", help="Input root or Phase 3 output dir."),
    output_dir: str = typer.Option("output", "--output", help="Output directory."),
    steps: str = typer.Option(
        "extract-text,chunk,llm,load,validate",
        "--steps",
        help="Comma-separated steps: extract-text,chunk,triage,llm,load,resolve,timeline,thread,validate.",
    ),
    llm_provider: str = typer.Option(
        "ollama",
        "--llm-provider",
        help="LLM provider for the llm step: ollama, openai, or gemini (default: ollama).",
    ),
    llm_model: str = typer.Option(
        "llama3",
        "--llm-model",
        help="LLM model name for the llm step (default: llama3).",
    ),
    llm_host: str = typer.Option(
        DEFAULT_OLLAMA_HOST,
        "--llm-host",
        help=f"Ollama host for --llm-provider=ollama (default: {DEFAULT_OLLAMA_HOST}).",
    ),
    llm_openai_base_url: str = typer.Option(
        DEFAULT_OPENAI_BASE_URL,
        "--llm-openai-base-url",
        help=f"OpenAI API base URL for --llm-provider=openai (default: {DEFAULT_OPENAI_BASE_URL}).",
    ),
    llm_gemini_base_url: str = typer.Option(
        DEFAULT_GEMINI_BASE_URL,
        "--llm-gemini-base-url",
        help=f"Gemini API base URL for --llm-provider=gemini (default: {DEFAULT_GEMINI_BASE_URL}).",
    ),
    llm_timeout: int = typer.Option(
        120,
        "--llm-timeout",
        help="LLM request timeout in seconds for the llm step (default: 120).",
    ),
    triage_keyword_packs_dir: str = typer.Option(
        None,
        "--triage-keyword-packs-dir",
        help="Optional keyword pack directory for triage (directory of .txt files).",
    ),
    triage_max_llm_chunks: int = typer.Option(
        None,
        "--triage-max-llm-chunks",
        help="Cap total chunks selected for LLM during triage.",
    ),
    triage_max_llm_chunks_per_file: int = typer.Option(
        None,
        "--triage-max-llm-chunks-per-file",
        help="Cap chunks per file_id selected for LLM during triage.",
    ),
    triage_max_llm_tokens: int = typer.Option(
        None,
        "--triage-max-llm-tokens",
        help="Cap estimated tokens selected for LLM during triage.",
    ),
    triage_allow_file_ids: str = typer.Option(
        None,
        "--triage-allow-file-ids",
        help="Optional newline-delimited file_id allowlist path for triage.",
    ),
    triage_deny_file_ids: str = typer.Option(
        None,
        "--triage-deny-file-ids",
        help="Optional newline-delimited file_id denylist path for triage.",
    ),
    triage_ner: bool = typer.Option(
        False,
        "--triage-ner",
        help="Enable optional local NER (spaCy) during triage (off by default).",
    ),
    triage_ner_model: str = typer.Option(
        "en_core_web_sm",
        "--triage-ner-model",
        help="spaCy model name for --triage-ner (default: en_core_web_sm).",
    ),
    triage_route_large_threshold: float = typer.Option(
        0.75,
        "--triage-route-large-threshold",
        help="Score threshold for routing to llm_large during triage.",
    ),
    triage_route_skip_threshold: float = typer.Option(
        0.10,
        "--triage-route-skip-threshold",
        help="Score threshold below which triage routes to skip.",
    ),
    triage_ml_route_model: str = typer.Option(
        None,
        "--triage-ml-route-model",
        help="Optional trained route model artifact (.pkl) for triage ML routing.",
    ),
    triage_ml_route_mode: str = typer.Option(
        "off",
        "--triage-ml-route-mode",
        help="ML routing mode for triage: off, report-only, shadow, or full (default: off).",
    ),
    triage_ml_route_skip_threshold: float = typer.Option(
        0.90,
        "--triage-ml-route-skip-threshold",
        help="Only allow ML skip when P(skip) >= this threshold during triage.",
    ),
    triage_ml_route_large_threshold: float = typer.Option(
        0.80,
        "--triage-ml-route-large-threshold",
        help="Allow ML llm_large when P(llm_large) >= this threshold during triage.",
    ),
    no_interactive: bool = typer.Option(
        False,
        "--no-interactive",
        help="Disable interactive prompts (currently unused).",
    ),
):
    _ = ctx, no_interactive
    selected = [step.strip() for step in steps.split(",") if step.strip()]
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    text_output_dir = output_path / "text"
    chunks_path = text_output_dir / "chunks.jsonl"
    triage_path = output_path / "triage.jsonl"
    triage_small_chunks_path = output_path / "chunks.llm_small.jsonl"
    triage_large_chunks_path = output_path / "chunks.llm_large.jsonl"
    stage_timings_path = output_path / "stage_timings.json"
    stage_timings_ms = {}
    triage_ran = False
    entities_path = output_path / "entities.jsonl"
    events_path = output_path / "events.jsonl"
    conversations_path = output_path / "conversations.jsonl"
    db_path = output_path / "store.sqlite"

    def _record_stage_timing(stage_name: str, started: float):
        stage_timings_ms[stage_name] = int(round((time.monotonic() - started) * 1000))
        stage_timings_path.write_text(
            json.dumps({"timings_ms": stage_timings_ms}, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )

    if "extract-text" in selected:
        stage_started = time.monotonic()
        _dispatch_to_main(
            "extract-text",
            [
                "--input",
                input_dir,
                "--phase1",
                str(output_path / "phase1.jsonl"),
                "--phase2",
                str(output_path / "phase2_ocr.jsonl"),
                "--output-dir",
                str(text_output_dir),
            ],
            phase3_main,
        )
        _record_stage_timing("extract-text", stage_started)
    if "chunk" in selected:
        stage_started = time.monotonic()
        _dispatch_to_main(
            "chunk",
            [
                "--input",
                str(text_output_dir),
                "--output",
                str(chunks_path),
            ],
            phase4_main,
        )
        _record_stage_timing("chunk", stage_started)
    if "triage" in selected:
        stage_started = time.monotonic()
        triage_args = [
            "--input",
            str(chunks_path),
            "--output-dir",
            str(output_path),
            "--route-large-threshold",
            str(triage_route_large_threshold),
            "--route-skip-threshold",
            str(triage_route_skip_threshold),
            "--ml-route-mode",
            str(triage_ml_route_mode),
            "--ml-route-skip-threshold",
            str(triage_ml_route_skip_threshold),
            "--ml-route-large-threshold",
            str(triage_ml_route_large_threshold),
            "--small-output",
            triage_small_chunks_path.name,
            "--large-output",
            triage_large_chunks_path.name,
        ]
        if triage_ml_route_model:
            triage_args.extend(["--ml-route-model", str(triage_ml_route_model)])
        if triage_keyword_packs_dir:
            triage_args.extend(["--keyword-packs-dir", str(triage_keyword_packs_dir)])
        if triage_max_llm_chunks is not None:
            triage_args.extend(["--max-llm-chunks", str(triage_max_llm_chunks)])
        if triage_max_llm_chunks_per_file is not None:
            triage_args.extend(["--max-llm-chunks-per-file", str(triage_max_llm_chunks_per_file)])
        if triage_max_llm_tokens is not None:
            triage_args.extend(["--max-llm-tokens", str(triage_max_llm_tokens)])
        if triage_allow_file_ids:
            triage_args.extend(["--allow-file-ids", str(triage_allow_file_ids)])
        if triage_deny_file_ids:
            triage_args.extend(["--deny-file-ids", str(triage_deny_file_ids)])
        if triage_ner:
            triage_args.append("--ner")
            triage_args.extend(["--ner-model", str(triage_ner_model)])
        _dispatch_to_main("triage", triage_args, triage_main)
        _record_stage_timing("triage", stage_started)
        triage_ran = True
    if "llm" in selected:
        stage_started = time.monotonic()
        llm_common_args = [
            "--provider",
            str(llm_provider),
            "--model",
            str(llm_model),
            "--host",
            str(llm_host),
            "--openai-base-url",
            str(llm_openai_base_url),
            "--gemini-base-url",
            str(llm_gemini_base_url),
            "--timeout",
            str(llm_timeout),
        ]
        llm_chunks_path = chunks_path
        if "triage" in selected and triage_small_chunks_path.exists():
            llm_chunks_path = triage_small_chunks_path
        _dispatch_to_main(
            "llm entities",
            ["--input", str(llm_chunks_path), "--output", str(entities_path), *llm_common_args],
            llm_entities_main,
        )
        _dispatch_to_main(
            "llm events",
            ["--input", str(llm_chunks_path), "--output", str(events_path), *llm_common_args],
            llm_events_main,
        )
        _dispatch_to_main(
            "llm conversations",
            ["--input", str(llm_chunks_path), "--output", str(conversations_path), *llm_common_args],
            llm_conversations_main,
        )
        _record_stage_timing("llm", stage_started)
    if "load" in selected:
        stage_started = time.monotonic()
        _run_load_stage(
            entities_path,
            events_path,
            conversations_path,
            db_path,
            overwrite=False,
        )
        _record_stage_timing("load", stage_started)
    if "resolve" in selected:
        stage_started = time.monotonic()
        _dispatch_to_main(
            "resolve",
            ["--db", str(db_path), "--reset"],
            resolve_people_main,
        )
        _record_stage_timing("resolve", stage_started)
    if "timeline" in selected:
        stage_started = time.monotonic()
        timeline_args = ["--db", str(db_path)]
        if chunks_path.exists():
            timeline_args.extend(["--chunks", str(chunks_path)])
        manifest_path = output_path / "manifest.jsonl"
        if manifest_path.exists():
            timeline_args.extend(["--manifest", str(manifest_path)])
        _dispatch_to_main("timeline", timeline_args, timeline_main)
        _record_stage_timing("timeline", stage_started)
    if "thread" in selected:
        stage_started = time.monotonic()
        thread_args = ["--db", str(db_path)]
        if chunks_path.exists():
            thread_args.extend(["--chunks", str(chunks_path)])
        manifest_path = output_path / "manifest.jsonl"
        if manifest_path.exists():
            thread_args.extend(["--manifest", str(manifest_path)])
        _dispatch_to_main("thread", thread_args, thread_main)
        _record_stage_timing("thread", stage_started)
    if "validate" in selected:
        stage_started = time.monotonic()
        validate_args = [
            "--chunks",
            str(chunks_path),
            "--entities",
            str(entities_path),
            "--events",
            str(events_path),
            "--conversations",
            str(conversations_path),
            "--phase3",
            str(text_output_dir),
        ]
        if triage_ran:
            validate_args.extend(["--triage", str(triage_path)])
        if stage_timings_ms:
            validate_args.extend(["--timings", str(stage_timings_path)])
        _dispatch_to_main(
            "validate",
            validate_args,
            phase7_main,
        )
        _record_stage_timing("validate", stage_started)


def main():
    app()


if __name__ == "__main__":
    main()

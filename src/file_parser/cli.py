import sys
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
from loaders.load_conversations import main as load_conversations_main
from loaders.load_entities import main as load_entities_main
from loaders.load_events import main as load_events_main
from loaders.load_identity_signals import main as load_identity_signals_main
from loaders.load_manifest import main as load_manifest_main
from text_extraction.phase3_extract_text import main as phase3_main
from timeline.phase9_stitch_timeline import main as timeline_main

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


@app.command("validate", help="Run Phase 7 sanity checks.")
def validate(ctx: typer.Context):
    _dispatch_to_main("validate", list(ctx.args), phase7_main)


@app.command("resolve", help="Run people entity resolution (Phase 8).")
def resolve(ctx: typer.Context):
    _dispatch_to_main("resolve", list(ctx.args), resolve_people_main)


@app.command("timeline", help="Normalize event dates and stitch timelines (Phase 9).")
def timeline(ctx: typer.Context):
    _dispatch_to_main("timeline", list(ctx.args), timeline_main)


@app.command("run", help="Run extract-text, chunk, llm, load, and validate steps.")
def run(
    ctx: typer.Context,
    input_dir: str = typer.Option(..., "--input", help="Input root or Phase 3 output dir."),
    output_dir: str = typer.Option("output", "--output", help="Output directory."),
    steps: str = typer.Option(
        "extract-text,chunk,llm,load,validate",
        "--steps",
        help="Comma-separated steps: extract-text,chunk,llm,load,resolve,timeline,validate.",
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
    text_output_dir = output_path / "text"
    chunks_path = text_output_dir / "chunks.jsonl"
    entities_path = output_path / "entities.jsonl"
    events_path = output_path / "events.jsonl"
    conversations_path = output_path / "conversations.jsonl"
    db_path = output_path / "store.sqlite"

    if "extract-text" in selected:
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
    if "chunk" in selected:
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
    if "llm" in selected:
        _dispatch_to_main(
            "llm entities",
            ["--input", str(chunks_path), "--output", str(entities_path)],
            llm_entities_main,
        )
        _dispatch_to_main(
            "llm events",
            ["--input", str(chunks_path), "--output", str(events_path)],
            llm_events_main,
        )
        _dispatch_to_main(
            "llm conversations",
            ["--input", str(chunks_path), "--output", str(conversations_path)],
            llm_conversations_main,
        )
    if "load" in selected:
        _run_load_stage(
            entities_path,
            events_path,
            conversations_path,
            db_path,
            overwrite=False,
        )
    if "resolve" in selected:
        _dispatch_to_main(
            "resolve",
            ["--db", str(db_path), "--reset"],
            resolve_people_main,
        )
    if "timeline" in selected:
        timeline_args = ["--db", str(db_path)]
        if chunks_path.exists():
            timeline_args.extend(["--chunks", str(chunks_path)])
        manifest_path = output_path / "manifest.jsonl"
        if manifest_path.exists():
            timeline_args.extend(["--manifest", str(manifest_path)])
        _dispatch_to_main("timeline", timeline_args, timeline_main)
    if "validate" in selected:
        _dispatch_to_main(
            "validate",
            [
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
            ],
            phase7_main,
        )


def main():
    app()


if __name__ == "__main__":
    main()

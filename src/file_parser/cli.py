import sys

import typer

from file_parser.manifest_builder import main as manifest_main
from file_parser.phase1_detect import main as phase1_main
from file_parser.phase1_report import main as report_main
from file_parser.phase2_ocr import main as phase2_main
from file_parser.run_pipeline import main as pipeline_main
from chunking.phase4_chunk import main as phase4_main
from text_extraction.phase3_extract_text import main as phase3_main

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


@app.command("chunk", help="Run Phase 4 chunking for analysis.")
def chunk(ctx: typer.Context):
    _dispatch_to_main("chunk", list(ctx.args), phase4_main)


@app.command("validate", help="Placeholder for future Phase 7 validation script.")
def validate():
    raise typer.BadParameter("Phase 7 validation is not implemented yet.")


def main():
    app()


if __name__ == "__main__":
    main()

# TODO: Immediate (Codex Execution Plan)

This file defines the *next executable milestones* for the Parsnip pipeline.  
Work through items **in order** unless explicitly told otherwise.

---

## Phase 3 — Unified Text Extraction (PRIORITY)

### 3.1 Create a new module
Add a new module:

src/text_extraction/
- phase3_extract_text.py

This should:
- Read phase1 detection output (JSONL).
- For each file_id:
  - If classification == "text":
    - Extract text using pypdf page-by-page.
  - If classification in ["scanned", "mixed", "unknown"]:
    - Use existing OCR outputs from Phase 2.
- Output a **single normalized record per PDF** with schema below.

---

### 3.2 Define canonical document schema (REQUIRED)

Each output record must look like:

{
  "file_id": str,
  "virtual_path": str,
  "classification": "text|scanned|mixed|unknown",
  "page_count": int,
  "quality_score": float,
  "pages": [
    {
      "page_index": int,
      "text": str,
      "source": "pdf_text" | "ocr",
      "confidence": float | null
    }
  ]
}

Notes:
- quality_score should roughly reflect:
  - % pages with non-empty text
  - OCR confidence if available
- If text extraction fails for a page, fallback to OCR for that page.

---

### 3.3 Sharded + compressed outputs

Instead of one giant file, write:

output/text/docs_0001.jsonl.gz  
output/text/docs_0002.jsonl.gz  
...

Rules:
- Shard by 5,000 documents per file.
- Use gzip compression.
- Maintain a small manifest file:
  output/text/manifest.json

Example manifest entry:
{
  "shard": "docs_0001.jsonl.gz",
  "start_index": 0,
  "end_index": 4999,
  "doc_count": 5000
}

---

### 3.4 Resume behavior

phase3_extract_text.py must:
- Support --resume
- Track processed file_ids in a SQLite DB (similar to earlier phases).
- Be restart-safe.

---

## Phase 4 — Chunking for Analysis

Create:

src/chunking/phase4_chunk.py

Given the unified text output, produce chunks.jsonl with:

{
  "chunk_id": str,
  "file_id": str,
  "page_start": int,
  "page_end": int,
  "text": str,
  "signals": {
    "likely_dialogue": bool,
    "has_dates": bool,
    "has_names": bool,
    "low_quality": bool
  }
}

Chunking rules (initial version):
- Default chunk = 2 pages at a time with 1-page overlap.
- If a page contains dialogue markers (e.g., "Name:", "—", quotes), create smaller chunks.

---

## Phase 5 — LLM Extraction (MVP)

Create a new folder:

src/llm/

Add three scripts (initial minimal versions):

1) extract_entities.py  
2) extract_events.py  
3) extract_conversations.py  

Each script should:
- Read chunks.jsonl
- Call a locally hosted LLM (Ollama, llama.cpp, or vLLM — user choice)
- Output strict JSONL:
  - entities.jsonl
  - events.jsonl
  - conversations.jsonl

Every record **must include evidence**:

{
  "file_id": "...",
  "chunk_id": "...",
  "page_range": [x, y],
  "quote": "short supporting text",
  "confidence": float
}

---

## Phase 6 — Minimal Storage Layer

Create a lightweight local store (no UI yet):

Use SQLite with tables:
- entities
- events
- conversations
- mentions (entity -> chunk mapping)

Write small loaders that ingest the JSONL outputs into these tables.

---

## Phase 7 — Sanity Checks

Add a validation script that reports:
- % chunks yielding at least one entity
- % chunks yielding at least one event
- rate of invalid JSON from LLM
- rate of empty text pages

---

## Definition of Done for Immediate Phase

You are done when:
- Every PDF produces normalized text output.
- You can run chunking end-to-end on a subset of files.
- You can run LLM extraction on chunks and load results into SQLite.

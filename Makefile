.PHONY: all manifest phase1 report phase2 phase3 test clean clean-all print-output

INPUT ?= /path/to/input
OUT_DIR ?= output
MANIFEST ?= $(OUT_DIR)/manifest.jsonl
PHASE1 ?= $(OUT_DIR)/phase1.jsonl
PHASE2 ?= $(OUT_DIR)/phase2_ocr.jsonl
TEXT_OUT ?= $(OUT_DIR)/text
PHASE3_COMPRESSION ?= zstd
CLEAN_OUTPUTS ?= $(MANIFEST) $(PHASE1) $(PHASE2) $(TEXT_OUT)

all: manifest phase1 report phase2 phase3

manifest:
	PYTHONPATH=src python -m file_parser.manifest_builder --input $(INPUT) --output $(MANIFEST)

phase1:
	PYTHONPATH=src python -m file_parser.phase1_detect --input $(INPUT) --manifest $(MANIFEST) --output $(PHASE1)

report:
	PYTHONPATH=src python -m file_parser.phase1_report --input $(PHASE1)

phase2:
	PYTHONPATH=src python -m file_parser.phase2_ocr --input $(INPUT) --phase1 $(PHASE1) --output $(PHASE2)

phase3:
	PYTHONPATH=src python -m text_extraction.phase3_extract_text --input $(INPUT) --phase1 $(PHASE1) --phase2 $(PHASE2) --output-dir $(TEXT_OUT) --compression $(PHASE3_COMPRESSION)

test:
	PYTHONPATH=src pytest -q

print-output:
	@printf '%s\n' $(CLEAN_OUTPUTS)

clean:
	@rm -rf $(CLEAN_OUTPUTS)

clean-all:
	@rm -rf $(OUT_DIR)

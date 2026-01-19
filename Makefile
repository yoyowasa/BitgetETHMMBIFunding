LOG_DIR ?= logs
ARTIFACT_DIR ?= artifacts
REPORT ?= $(ARTIFACT_DIR)/validate_report.json

REQUIRE_FILES ?= system*.jsonl,orders*.jsonl,fills*.jsonl,mm_*.jsonl

STRICT_FLAGS ?= \
  --require-ticket-events \
  --halt-strict \
  --controlled-grace-sec 6 \
  --hedge-max-tries 2

.PHONY: validate-logs-loose validate-logs-strict

validate-logs-loose:
	mkdir -p $(ARTIFACT_DIR)
	python tools/validate_logs.py $(LOG_DIR) \
	  --json-out $(REPORT) \
	  --require-files "$(REQUIRE_FILES)"

validate-logs-strict:
	mkdir -p $(ARTIFACT_DIR)
	python tools/validate_logs.py $(LOG_DIR) \
	  --json-out $(REPORT) \
	  --require-files "$(REQUIRE_FILES)" \
	  $(STRICT_FLAGS)

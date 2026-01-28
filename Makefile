LOG_DIR ?= logs
ARTIFACT_DIR ?= artifacts
REPORT ?= $(ARTIFACT_DIR)/validate_report.json

REQUIRE_FILES ?= system*.jsonl,orders*.jsonl,fills*.jsonl,mm_*.jsonl

CONFIG ?= config.example.yaml
HEDGE_MAX_TRIES ?= $(shell python -c "import yaml; c=yaml.safe_load(open('$(CONFIG)','r',encoding='utf-8')); print(c['hedge']['hedge_max_tries'])")
CONTROLLED_GRACE ?= $(shell python -c "import yaml; c=yaml.safe_load(open('$(CONFIG)','r',encoding='utf-8')); print(float(c['risk']['controlled_reconnect_grace_sec'])+1.0)")

STRICT_FLAGS ?= \
  --require-ticket-events \
  --halt-strict \
  --controlled-grace-sec $(CONTROLLED_GRACE) \
  --hedge-max-tries $(HEDGE_MAX_TRIES)

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

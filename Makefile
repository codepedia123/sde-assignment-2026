SCENARIO ?= scenarios/public_01_dev.yaml
NAME ?= $(basename $(notdir $(SCENARIO)))

baseline:
	python -m sim baseline --scenario $(SCENARIO) --run-dir runs/base_$(NAME)
serve:
	python -m sim serve --scenario $(SCENARIO) --run-dir runs/mine_$(NAME) --force
grade:
	mkdir -p reports && python -m sim grade --run runs/mine_$(NAME) --baseline runs/base_$(NAME) --out reports/$(NAME).md
test:
	python -m pytest -q tests

crash-test:
	python scripts/crash_recovery_demo.py --output reports/crash_recovery.json

audit-example:
	python scripts/audit_query.py .controller-state/public_02_incidents.sqlite3 ks_cancel

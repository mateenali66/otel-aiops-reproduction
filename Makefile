# Reproduction package for "Evaluating ML-Based Anomaly Detection on Unified OpenTelemetry
# Telemetry" (IEEE Access, 10.1109/ACCESS.2026.3705430), implementing FDES v1.0.0-draft.
#
#   make fetch          download Zenodo 10.5281/zenodo.22078287 and verify the md5
#   make smoke          one signal, one fold, minutes on a laptop CPU
#   make verify         compare out/smoke against expected/ (exit 1 on mismatch)
#   make verify-archive recompute the archived tables from the archived raw scores
#   make reproduce      full 8 models x 3 signals x 5 folds (prints the estimate first)
#   make verify MODE=full
#   make pilot          run the example detector plugin under the FDES procedure
#   make check          run the FDES checks on your own alert or score CSVs (no Zenodo needed)
#   make test           unit tests for the check and pilot paths (no Zenodo needed)
#   make docker-build / docker-smoke

PYTHON ?= python3
MODE   ?= smoke
OUT    ?= out/$(MODE)
IMAGE  ?= otel-aiops-reproduction:3.1.3
DETECTOR ?= detectors.example_isolation_forest:ExampleIsolationForest
SIGNAL ?= logs
FOLD   ?= 1

# `make check` defaults to the shipped sample data so it runs before you export anything.
# Point it at your own files: make check ALERTS=my_alerts.csv INCIDENTS=my_incidents.csv \
#   BUCKET=5m FROM=2026-03-01T00:00:00Z TO=2026-03-08T00:00:00Z
# For a score series, pass SCORES= instead of ALERTS=.
ALERTS    ?= examples/alerts_good.csv
INCIDENTS ?= examples/incidents.csv
BUCKET    ?= 5m
FROM      ?= 2026-03-01T00:00:00Z
TO        ?= 2026-03-08T00:00:00Z
SCORES    ?=

.PHONY: help fetch smoke reproduce verify verify-archive estimate pilot check test examples expected docker-build docker-smoke clean

help:
	@sed -n '2,14p' Makefile

fetch:
	$(PYTHON) bin/reproduce.py fetch $(if $(FROM_ZIP),--from-zip $(FROM_ZIP))

smoke:
	$(PYTHON) bin/reproduce.py smoke --out out/smoke

reproduce:
	$(PYTHON) bin/reproduce.py reproduce --out out/full $(if $(FOLDS),--folds $(FOLDS)) $(if $(SIGNALS),--signals $(SIGNALS)) $(if $(MODELS),--models $(MODELS))

estimate:
	$(PYTHON) bin/reproduce.py estimate

verify:
	$(PYTHON) bin/reproduce.py verify --out $(OUT)

verify-archive:
	$(PYTHON) bin/reproduce.py verify-archive

pilot:
	$(PYTHON) bin/reproduce.py pilot --detector $(DETECTOR) --signal $(SIGNAL) --fold $(FOLD)

check:
	$(PYTHON) bin/reproduce.py check $(if $(SCORES),--scores $(SCORES),--alerts $(ALERTS)) \
		--incidents $(INCIDENTS) --bucket $(BUCKET) \
		$(if $(FROM),--from $(FROM)) $(if $(TO),--to $(TO)) \
		$(if $(THRESHOLD),--threshold $(THRESHOLD)) $(if $(LABEL),--label $(LABEL))

test:
	$(PYTHON) -m unittest discover -s tests -v

examples:
	$(PYTHON) examples/make_examples.py

expected:
	$(PYTHON) bin/build_expected.py

docker-build:
	docker build -t $(IMAGE) .

docker-smoke: docker-build
	docker run --rm -v $(PWD)/data:/work/data -v $(PWD)/out:/work/out $(IMAGE) fetch
	docker run --rm -v $(PWD)/data:/work/data -v $(PWD)/out:/work/out $(IMAGE) smoke
	docker run --rm -v $(PWD)/data:/work/data -v $(PWD)/out:/work/out $(IMAGE) verify

clean:
	rm -rf out

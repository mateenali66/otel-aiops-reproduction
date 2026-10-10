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
#   make paper-tables   Tables 4 to 12 in the article's layout (RESULTS=archive for Zenodo's)
#   make figures        Figures 2 to 7 in the article's layout (RESULTS=archive for Zenodo's)
#   make docker-build / docker-smoke
#
# Once `make docker-build` has run, every target above runs inside that image, with data/
# and out/ mounted from the checkout. NO_DOCKER=1 runs the targets on the local Python
# instead.

PYTHON ?= $(shell command -v python3 >/dev/null 2>&1 && echo python3 || echo python)
DOCKER ?= docker
MODE   ?= smoke
OUT    ?= out/$(MODE)
IMAGE  ?= otel-aiops-reproduction:1.4.1
RESULTS ?= out/full/results
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

.PHONY: help fetch smoke reproduce verify verify-archive estimate pilot check test examples expected paper-tables figures docker-build docker-smoke clean

# Git Bash on Windows rewrites arguments that look like paths, including the container's
# /work. These two variables turn that off.
export MSYS_NO_PATHCONV := 1
export MSYS2_ARG_CONV_EXCL := *

# Inside the image, where `docker run ... $(IMAGE) smoke` reaches make through the
# ENTRYPOINT, the interpreter is called directly so make does not start docker again.
ifneq ($(wildcard /.dockerenv),)
NO_DOCKER := 1
endif
ifeq ($(NO_DOCKER),)
HAVE_IMAGE := $(shell $(DOCKER) image inspect $(IMAGE) >/dev/null 2>&1 && echo yes)
endif
ifeq ($(HAVE_IMAGE),yes)
RUN = $(DOCKER) run --rm -v "$(CURDIR)":/work -v "$(CURDIR)/data":/work/data \
	-v "$(CURDIR)/out":/work/out -w /work --entrypoint python $(IMAGE)
ROUTE = container $(IMAGE)
else
RUN = $(PYTHON)
ROUTE = local $(PYTHON)
endif

help:
	@sed -n '2,19p' Makefile
	@echo "route: $(ROUTE)"

fetch:
	$(RUN) bin/reproduce.py fetch $(if $(FROM_ZIP),--from-zip $(FROM_ZIP))

smoke:
	$(RUN) bin/reproduce.py smoke --out out/smoke

reproduce:
	$(RUN) bin/reproduce.py reproduce --out out/full $(if $(FOLDS),--folds $(FOLDS)) $(if $(SIGNALS),--signals $(SIGNALS)) $(if $(MODELS),--models $(MODELS))

estimate:
	$(RUN) bin/reproduce.py estimate

verify:
	$(RUN) bin/reproduce.py verify --out $(OUT)

verify-archive:
	$(RUN) bin/reproduce.py verify-archive

pilot:
	$(RUN) bin/reproduce.py pilot --detector $(DETECTOR) --signal $(SIGNAL) --fold $(FOLD)

check:
	$(RUN) bin/reproduce.py check $(if $(SCORES),--scores $(SCORES),--alerts $(ALERTS)) \
		--incidents $(INCIDENTS) --bucket $(BUCKET) \
		$(if $(FROM),--from $(FROM)) $(if $(TO),--to $(TO)) \
		$(if $(THRESHOLD),--threshold $(THRESHOLD)) $(if $(LABEL),--label $(LABEL))

test:
	$(RUN) -m unittest discover -s tests -v

examples:
	$(RUN) examples/make_examples.py

expected:
	$(RUN) bin/build_expected.py

paper-tables:
	$(RUN) bin/reproduce.py paper-tables --results $(RESULTS)

figures:
	$(RUN) bin/reproduce.py figures --results $(RESULTS)

docker-build:
	docker build -t $(IMAGE) .

docker-smoke: docker-build
	docker run --rm -v "$(CURDIR)"/data:/work/data -v "$(CURDIR)"/out:/work/out $(IMAGE) fetch
	docker run --rm -v "$(CURDIR)"/data:/work/data -v "$(CURDIR)"/out:/work/out $(IMAGE) smoke
	docker run --rm -v "$(CURDIR)"/data:/work/data -v "$(CURDIR)"/out:/work/out $(IMAGE) verify

clean:
	rm -rf out

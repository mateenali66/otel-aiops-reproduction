# Reproduction package: ML anomaly detection on unified OpenTelemetry telemetry

One-command reproduction of the IEEE Access article

> M. A. Anjum, "Evaluating ML-Based Anomaly Detection on Unified OpenTelemetry Telemetry:
> An Empirical Study Across Traces, Metrics, and Logs," IEEE Access, vol. 14,
> pp. 93576-93608, 2026. DOI [10.1109/ACCESS.2026.3705430](https://doi.org/10.1109/ACCESS.2026.3705430)

from its published Zenodo artifact (DOI [10.5281/zenodo.22078287](https://doi.org/10.5281/zenodo.22078287),
record v3.1.3), in a pinned container, with fixed seeds and documented expected output. It is
the reference implementation of the **Failure Detection Evaluation Specification (FDES)
v1.0.0-draft** ([github.com/mateenali66/failure-detection-evaluation-spec](https://github.com/mateenali66/failure-detection-evaluation-spec)),
and it ships a plugin interface so an operator can put their own detector through the same
procedure and get a pass or exclude verdict against the specification.

## Quick start

```bash
git clone <this repository> && cd reproduction
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt   # or: make docker-build
make fetch            # downloads the Zenodo zip (63 MB), verifies md5, unpacks to data/
make smoke            # one signal, one fold: a few minutes on a laptop CPU
make verify           # compares out/smoke against expected/, exit code 1 on mismatch
make verify-archive   # regenerates the archived tables from the archived raw scores (seconds)
make pilot            # runs the example detector plugin under the FDES procedure
```

Container route (no local Python needed):

```bash
make docker-build     # python:3.11-slim pinned by digest, CPU torch, exact pins
make docker-smoke     # fetch, smoke and verify inside the container; data/ and out/ are volumes
```

Full reproduction (8 models x 3 signals x 5 folds; see runtimes below before starting):

```bash
make reproduce                       # prints the estimate, then runs everything
make reproduce FOLDS=1 SIGNALS=logs  # or one slice at a time, then merge by hand
make verify MODE=full
```

The single entry point behind every target is `bin/reproduce.py`; `python bin/reproduce.py -h`
lists the subcommands. No cloud account, S3 bucket or SageMaker role is needed: the artifact's
SageMaker orchestration (`run_pipeline.py`) is not used, and `training.py` is driven directly
in its `--local` code path with CPU forced.

## What gets reproduced, and from what

`make fetch` downloads `otel-aiops-benchmark.zip` from Zenodo record 22078287 and checks it
against the md5 recorded in `expected/zenodo_checksums.json` (`ef2cd3b74b17d7984e56fe09b10684eb`,
62,897,450 bytes, taken from the Zenodo API on 2026-08-29). The zip holds the processed
60-second feature tables for the three signals (`data/features/*.parquet`, 58 MB), the
article's per-fold results and raw anomaly scores, and the pipeline code. The pipeline is
executed as archived; this repository adds only the wrapper (seeding, CPU forcing, budget
control, table building, verification, the FDES checks and the detector plugin interface).

| Output file (under `out/<mode>/`) | Article table or figure | Produced by |
|---|---|---|
| `results/model_results.csv` | per-fold rows behind Table 4 (cooldown excluded) | artifact `training.py` |
| `results/model_results_with_cooldown.csv` | cooldown-included ablation (Tables 11, 12 right column) | artifact `training.py` |
| `tables/table4_single_signal.csv` | **Table 4** (mean and std over folds), Figures 2 and 3 | `fdes/tables.py` |
| `tables/table5_fusion.csv` | **Table 5** late fusion, Figures 4 and 5 (needs a run with 2+ signals) | `fdes/tables.py` from `results/fusion_results.csv` |
| `tables/table7_per_fault_recall.csv` | **Table 7** per-fault recall, Figure 6 | `fdes/tables.py` from `results/per_fault_type_results.csv` |
| `tables/table8_training_time.csv` | **Table 8**, Figure 7 (times will reflect your CPU) | `fdes/tables.py` |
| `tables/below_chance.json` | the "three of eight" (37.5 percent) figure: models with mean AUC-ROC below 0.5 on every signal (Section V-B, FDES section 8) | `fdes/checks.py` |
| `tables/fdes_checks.csv` | FDES sections 7 and 8 per (model, signal, fold): predict-all floor, AUC vs 0.5, flag-everything guard, Table 12 degenerate rule | `fdes/checks.py` |
| `tables/friedman_ranking.csv` | Friedman ranks per signal (Section V-C; needs 3+ folds and fusion results) | artifact `significance_tests.py` |
| `results/optuna_results.csv` | Table 3 search spaces, selected hyperparameters | artifact `training.py` |
| `results/raw_scores/*.npy` | inputs to Table 10 (prevalence re-scoring) and Table 12 via the artifact's `prevalence_sensitivity.py` and `score_based_analyses.py` | artifact `training.py` |
| `out/pilot/<detector>_<signal>_fold<k>/pilot_report.md` | FDES verdict for your own detector | `fdes/protocol.py` |

Figures 2 to 7 are regenerated from the CSVs above with the artifact's `code/figures/*.py`
(matplotlib and seaborn are pinned for that purpose); Figures 8 to 16 (feature importance,
separability and training-dynamics diagnostics) come from `code/pipeline/ae_diagnostics.py`
and `code/analysis/supervised_baseline.py`, which are not wrapped here. Tables 13 to 16
(feature ablation, log representation, window sensitivity, external validity) are separate
studies with their own archived scripts and are out of scope for `make reproduce`.

## Expected output

`expected/` is generated from the fetched artifact by `make expected` (`bin/build_expected.py`)
and is what `make verify` compares against:

| File | Content |
|---|---|
| `model_results_per_fold.csv` | the 120 archived per-fold rows (8 models x 3 signals x 5 folds) |
| `table4_single_signal.csv` | Table 4 as mean and std; matches the printed table to three decimals |
| `below_chance.json` | `["CNN1D_AE", "LSTM_AE", "LSTM_VAE"]`, 3 of 8 = 0.375 |
| `friedman_ranking.csv` | archived 8-model Friedman ranks; DAGMM rank 1 on every signal, p < 0.001 |
| `metric_reconciliation.csv` | archived Table 12 support (AUC, prevalence, predict-all floor, degenerate flag) |
| `zenodo_checksums.json` | md5 and size of the record's files |

Headline values the run must land on (from Table 4): DAGMM metrics F1 0.906 / AUC-ROC 0.960,
DAGMM traces 0.818 / 0.897, DAGMM logs 0.811 / 0.889, Isolation Forest logs 0.579 / 0.640,
and AUC-ROC below 0.5 on all three signals for LSTM-AE (0.207, 0.308, 0.215), CNN1D-AE
(0.216, 0.387, 0.400) and LSTM-VAE (0.171, 0.299, 0.266).

## What `make verify` compares

Exit code 0 on pass, 1 on any mismatch; the report is written to `out/<mode>/verify_report.txt`.

Smoke mode (`out/smoke`):

- Isolation Forest and One-Class SVM, logs, fold 1, run at the published Optuna budget
  (10 trials): precision, recall, F1, AUC-ROC and PR-AUC each within **0.02** of the archived
  per-fold row. In practice they match to four decimals (see below).
- DAGMM, logs, fold 1, run at a reduced budget (2 Optuna trials instead of 25, epochs capped
  at 30): checked for presence and finite metrics and passed through the FDES checks, **not
  compared numerically**. A reduced search cannot be expected to land on the published number
  (the recorded venv run gave F1 0.793, AUC-ROC 0.908; the container run on the same machine
  gave F1 0.806, AUC-ROC 0.908; the archived 25-trial values are 0.877 and 0.939). The two
  classical rows were identical to four decimals in both environments.

The recorded smoke run (`runs/2026-08-29-smoke-m4pro/`) matched both classical rows to four
decimals. Its `fdes_checks.csv` also shows the section 8b guard doing its job: One-Class SVM
on logs scores F1 0.565 against a predict-all floor of 0.566 with recall 0.975 and is marked
EXCLUDE, while Isolation Forest (F1 0.580, recall 0.742) and DAGMM (F1 0.793, AUC 0.908) PASS.

Full mode (`out/full`):

- Per (model, signal) mean F1 and mean AUC-ROC against Table 4: classical models within 0.02;
  deep models within max(0.05, 2 x published std). Deep models were trained on GPUs for the
  article and on CPU here, so bit-for-bit agreement is not claimed; the seed policy makes the
  run deterministic on a given machine, not across hardware and BLAS builds.
- The below-chance set must equal `{CNN1D_AE, LSTM_AE, LSTM_VAE}` (3 of 8, 37.5 percent).
- Friedman: DAGMM must rank first on each signal with p < 0.05.

`make verify-archive` is the FDES section 9 requirement ("archived tables regenerate exactly
from the archived raw scores"): it recomputes `metric_reconciliation.csv`, Table 4, the
below-chance set and the Friedman ranks from the archived scores and per-fold CSVs and
requires equality to three decimals. It runs in about two seconds.

## Runtimes and hardware

Measured on an Apple M4 Pro (12 cores, 24 GB), CPU only, Python 3.11.14, torch 2.5.1,
with the venv route:

| Target | Wall clock | Notes |
|---|---|---|
| `make fetch` | 34 s | 63 MB download plus md5 and unzip |
| `make smoke` | 151 s (2.5 min) | logs signal, fold 1; the two classical models take 12 s, the reduced-budget DAGMM the rest |
| `make docker-smoke` (smoke inside the container) | 239 s (4.0 min) | same machine, arm64 image, verify PASS |
| `make verify` | under 2 s | |
| `make verify-archive` | 2 s | |
| `make pilot` | 2 s | example Isolation Forest plugin |
| `make reproduce` | **not run in this repository yet** | see the estimate below |

The full run was not executed here. The artifact records 41.8 hours of training time
(Optuna search plus final fit, summed over all 120 model-signal-fold cells) on the article's
SageMaker instances, with the deep models on NVIDIA A10G GPUs; the metrics signal (532k rows,
64 features) accounts for 19.9 of those hours. On a laptop CPU the classical models finish in
minutes per signal, and the deep models run several times slower than on the GPU. The working
estimate printed by `make reproduce` is 2 to 5 days on an 8-core CPU for the whole grid; use
`FOLDS=` and `SIGNALS=` to spread it across machines. This estimate is an extrapolation from
the smoke timing and the archived GPU times, not a measurement.

## Seed policy

Base seed 42 + fold id (fold 1 uses 43, fold 5 uses 47), applied by the wrapper to Python
`random`, NumPy and PyTorch before each fold's `train_signal()` call. Inside the artifact,
Optuna's TPE sampler is seeded with 42, Isolation Forest uses `random_state=42`, the
One-Class SVM subsample uses `RandomState(42)`, and the baseline (normal-only) split uses
`RandomState(42 + fold_id)`, all unchanged. Folds are partitioned by fault-injection
repetition (test repetitions {1,2}, {3,4}, {5,6}, {7,8}, {9,10}; validation repetition 10, or
8 for fold 5), never by random window sampling. Every run writes its seed policy, package
versions and hardware to `out/<mode>/run_manifest.json`.

## FDES v1.0.0-draft sections implemented

| Check | Spec section | Where |
|---|---|---|
| Predict-all baseline F1 = 2p/(1+p) reported next to every operating-point score | 2, 7 | `fdes/checks.py::predict_all_f1`, `tables/fdes_checks.csv` |
| Threshold-independent metrics from both families (AUC-ROC, PR-AUC) with random references (0.5, p) | 6, 7 | `fdes/checks.py::check_row` |
| Exclusion when AUC-ROC is at or below the random reference | 8a | `sec8a_auc_at_or_below_random`; model-level count in `below_chance.json` |
| Flag-everything guard: F1 within 5 percent of the floor with recall at or above 0.95 | 8b | `sec8b_flag_everything` |
| Degenerate-detector rule used in the article (AUC <= 0.55 and F1 >= 0.95 x floor) | 8 | `degenerate_table12_rule`, `metric_reconciliation_from_raw_scores` |
| Folds by repetition, threshold on a disjoint validation repetition, raw scores retained | 5 (steps 1 to 3) | artifact `training.py`; `fdes/protocol.py` for pilots |
| Prevalence re-scoring at 1, 5, 10, 20 percent | 5 (step 5) | artifact `code/analysis/prevalence_sensitivity.py` on `results/raw_scores/` |
| Episode-level detection | 5 (step 6) | artifact `training.py::evaluate_episode_detection` (`results/episode_results.csv`) |
| Published seed policy, per-fold results, regeneration from archived scores | 9 | this README, `expected/model_results_per_fold.csv`, `make verify-archive` |

The 5 percent margin in the section 8b check is this package's choice of "the evaluator's
stated margin"; change `FLOOR_MARGIN` in `fdes/checks.py` to use your own.

## Run against your own detector

The pilot path evaluates any detector under the FDES procedure on the archived telemetry
(or on your own feature table) and returns a verdict.

1. Subclass `detectors.base.Detector` in a module on `PYTHONPATH`:

   ```python
   from detectors.base import Detector

   class MyDetector(Detector):
       name = "my_detector"

       def fit(self, X_normal):          # normal windows only, already standardised
           self.model = ...
           return self

       def score(self, X):               # one score per window, higher = more anomalous
           return self.model.anomaly_score(X)
   ```

2. Run it on one signal and fold (repeat for the folds you want):

   ```bash
   python bin/reproduce.py pilot --detector my_module:MyDetector --signal metrics --fold 1
   ```

   Exit code 0 means PASS, 2 means EXCLUDE under section 8. The report
   (`out/pilot/<name>_<signal>_fold<k>/pilot_report.md`) prints every check with its reference
   value; `pilot_result.json` has the numbers and the fold assignment; the raw test scores and
   labels are saved as `.npy` for the prevalence re-scoring step.

3. To use your own telemetry, pass `--features path/to/features.parquet`. Required columns:
   `label` (0 or 1 per window), `rep` (repetition id 1 to 10, used for the fold split),
   `phase` (`baseline` for normal-only collection, anything else for the fault campaign),
   `timestamp`, `fault_type` (used for cooldown marking and per-fault reporting), plus
   numeric feature columns. Column names `service, run_id, testbed, count, fold` are treated
   as metadata and ignored as features.

The shipped example, `detectors/example_isolation_forest.py`, uses the hyperparameters Optuna
selected for the article's Isolation Forest on logs fold 1. Running it (`make pilot`)
reproduces that archived row exactly (precision 0.4755, recall 0.7420, F1 0.5796,
AUC-ROC 0.6355, PR-AUC 0.5321 on 10,449 cooldown-excluded windows), which is the
self-consistency check that the pilot protocol scores a detector the way the article did.

## Layout

```
bin/reproduce.py        single entry point (fetch, smoke, reproduce, verify, verify-archive, estimate, pilot)
bin/build_expected.py   regenerates expected/ from the fetched artifact
fdes/checks.py          FDES section 2, 6, 7, 8 checks
fdes/protocol.py        FDES section 5 procedure for pilots (reuses the artifact's split, cooldown, threshold and metric code)
fdes/tables.py          Table 4, 5, 7, 8, below-chance, Friedman
detectors/base.py       plugin interface; detectors/example_isolation_forest.py
expected/               archived values verify compares against
runs/                   recorded runs: manifest, verify reports, tables (evidence, not inputs)
Dockerfile              python:3.11-slim@sha256:1042b6... + CPU torch 2.5.1 + exact pins
data/, out/             created by fetch and the runs; git-ignored
```

## License

Wrapper code in this repository (`bin/`, `fdes/`, `detectors/`, `Makefile`, `Dockerfile`):
Apache-2.0. The Zenodo artifact fetched into `data/` and the derived tables in `expected/`:
CC-BY-4.0, attribution Mateen Ali Anjum, DOI 10.5281/zenodo.22078287. See `LICENSE`.

## Citation

```bibtex
@article{anjum2026otel,
  author  = {Anjum, Mateen Ali},
  title   = {Evaluating {ML}-Based Anomaly Detection on Unified {OpenTelemetry} Telemetry:
             An Empirical Study Across Traces, Metrics, and Logs},
  journal = {IEEE Access},
  volume  = {14},
  pages   = {93576--93608},
  year    = {2026},
  doi     = {10.1109/ACCESS.2026.3705430}
}

@dataset{anjum2026otelartifact,
  author    = {Anjum, Mateen Ali},
  title     = {{OpenTelemetry AIOps Benchmark}: {ML}-based Anomaly Detection across Traces,
               Metrics, and Logs (v3.1.3)},
  publisher = {Zenodo},
  year      = {2026},
  doi       = {10.5281/zenodo.22078287}
}
```

Specification: Failure Detection Evaluation Specification v1.0.0-draft,
https://github.com/mateenali66/failure-detection-evaluation-spec. Machine-readable metadata in
`CITATION.cff`.

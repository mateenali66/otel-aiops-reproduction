# Reproduction package: ML anomaly detection on unified OpenTelemetry telemetry

This repository reproduces the IEEE Access article

> M. A. Anjum, "Evaluating ML-Based Anomaly Detection on Unified OpenTelemetry Telemetry:
> An Empirical Study Across Traces, Metrics, and Logs," IEEE Access, vol. 14,
> pp. 93576-93608, 2026. DOI [10.1109/ACCESS.2026.3705430](https://doi.org/10.1109/ACCESS.2026.3705430)

from its published Zenodo artifact (DOI [10.5281/zenodo.22078287](https://doi.org/10.5281/zenodo.22078287),
record v3.1.3) with one command. It runs in a pinned container with fixed seeds and documented
expected output.

It is the reference implementation of the Failure Detection Evaluation Specification (FDES)
v1.0.0-draft ([github.com/mateenali66/failure-detection-evaluation-spec](https://github.com/mateenali66/failure-detection-evaluation-spec)).
It ships a plugin interface, so an operator can put their own detector through the same
procedure and get a verdict against the specification.

If what you actually want is to point the checks at your own monitoring data, skip to
[Check your own data](#check-your-own-data). That path needs two CSVs and no download.

## Quick start

```bash
git clone <this repository> && cd reproduction
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt   # or: make docker-build
make fetch            # downloads the Zenodo zip (63 MB), verifies md5, unpacks to data/
make smoke            # one signal, one fold: a few minutes on a laptop CPU
make verify           # compares out/smoke against expected/, exit code 1 on mismatch
make verify-archive   # regenerates the archived tables from the archived raw scores (seconds)
make pilot            # runs the example detector plugin under the FDES procedure
make check            # runs the FDES checks on the sample CSVs in examples/
make test             # unit tests for the check path
```

`make check` and `make test` need neither the Zenodo download nor a GPU. They need numpy,
pandas and scikit-learn from `requirements.txt` and nothing else.

The container route needs no local Python.

```bash
make docker-build     # python:3.11-slim pinned by digest, CPU torch, exact pins
make docker-smoke     # fetch, smoke and verify inside the container; data/ and out/ are volumes
```

The full reproduction covers 8 models x 3 signals x 5 folds. Read the runtimes section
before starting it.

```bash
make reproduce                       # prints the estimate, then runs everything
make reproduce FOLDS=1 SIGNALS=logs  # or one slice at a time, then merge by hand
make verify MODE=full
```

The single entry point behind every target is `bin/reproduce.py`. `python bin/reproduce.py -h`
lists the subcommands. No cloud account, S3 bucket or SageMaker role is needed. The artifact's
SageMaker orchestration (`run_pipeline.py`) is not used, and `training.py` is driven directly
in its `--local` code path with CPU forced.

## What gets reproduced, and from what

`make fetch` downloads `otel-aiops-benchmark.zip` from Zenodo record 22078287 and checks it
against the md5 recorded in `expected/zenodo_checksums.json` (`ef2cd3b74b17d7984e56fe09b10684eb`,
62,897,450 bytes, taken from the Zenodo API on 2026-08-29). The zip holds the processed
60-second feature tables for the three signals (`data/features/*.parquet`, 58 MB), the
article's per-fold results and raw anomaly scores, and the pipeline code.

The pipeline runs as archived. This repository adds only the wrapper (seeding, CPU forcing,
budget control, table building, verification, the FDES checks and the detector plugin interface).

| Output file (under `out/<mode>/`) | Article table or figure | Produced by |
|---|---|---|
| `results/model_results.csv` | per-fold rows behind Table 4 (cooldown excluded) | artifact `training.py` |
| `results/model_results_with_cooldown.csv` | cooldown-included ablation (Tables 11, 12 right column) | artifact `training.py` |
| `tables/table4_single_signal.csv` | Table 4 (mean and std over folds), Figures 2 and 3 | `fdes/tables.py` |
| `tables/table5_fusion.csv` | Table 5 late fusion, Figures 4 and 5 (needs a run with 2+ signals) | `fdes/tables.py` from `results/fusion_results.csv` |
| `tables/table7_per_fault_recall.csv` | Table 7 per-fault recall, Figure 6 | `fdes/tables.py` from `results/per_fault_type_results.csv` |
| `tables/table8_training_time.csv` | Table 8, Figure 7 (times will reflect your CPU) | `fdes/tables.py` |
| `tables/below_chance.json` | the three-of-eight (37.5 percent) figure, models with mean AUC-ROC below 0.5 on every signal (Section V-B, FDES section 8) | `fdes/checks.py` |
| `tables/fdes_checks.csv` | FDES sections 7 and 8 per (model, signal, fold), covering predict-all floor, AUC vs 0.5, flag-everything guard, Table 12 degenerate rule | `fdes/checks.py` |
| `tables/friedman_ranking.csv` | Friedman ranks per signal (Section V-C, needs 3+ folds and fusion results) | artifact `significance_tests.py` |
| `results/optuna_results.csv` | Table 3 search spaces, selected hyperparameters | artifact `training.py` |
| `results/raw_scores/*.npy` | inputs to Table 10 (prevalence re-scoring) and Table 12 via the artifact's `prevalence_sensitivity.py` and `score_based_analyses.py` | artifact `training.py` |
| `out/pilot/<detector>_<signal>_fold<k>/pilot_report.md` | FDES verdict for your own detector | `fdes/protocol.py` |
| `out/check/<name>/check_report.md` | FDES verdict for your own alert or score CSVs | `fdes/byod.py` |

Figures 2 to 7 are regenerated from the CSVs above with the artifact's `code/figures/*.py`
(matplotlib and seaborn are pinned for that purpose). Figures 8 to 15 (feature importance,
separability and training-dynamics diagnostics) come from `code/pipeline/ae_diagnostics.py`
and `code/analysis/supervised_baseline.py`, which are not wrapped here.

Tables 13 to 16 (feature ablation, log representation, window sensitivity, external validity)
are separate studies with their own archived scripts and are out of scope for `make reproduce`.

## Expected output

`make expected` (`bin/build_expected.py`) generates `expected/` from the fetched artifact.
`make verify` compares against it.

| File | Content |
|---|---|
| `model_results_per_fold.csv` | the 120 archived per-fold rows (8 models x 3 signals x 5 folds) |
| `table4_single_signal.csv` | Table 4 as mean and std, matches the printed table to three decimals |
| `below_chance.json` | `["CNN1D_AE", "LSTM_AE", "LSTM_VAE"]`, 3 of 8 = 0.375 |
| `friedman_ranking.csv` | archived 8-model Friedman ranks, DAGMM rank 1 on every signal, p < 0.001 |
| `metric_reconciliation.csv` | archived Table 12 support (AUC, prevalence, predict-all floor, degenerate flag) |
| `zenodo_checksums.json` | md5 and size of the record's files |

The run must land on the following Table 4 values. DAGMM scores F1 0.906 and AUC-ROC 0.960 on
metrics, 0.818 and 0.897 on traces, and 0.811 and 0.889 on logs. Isolation Forest scores
0.579 and 0.640 on logs. LSTM-AE (0.207, 0.308, 0.215), CNN1D-AE (0.216, 0.387, 0.400) and
LSTM-VAE (0.171, 0.299, 0.266) score AUC-ROC below 0.5 on all three signals.

## What `make verify` compares

`make verify` exits 0 on pass and 1 on any mismatch. It writes the report to
`out/<mode>/verify_report.txt`.

Smoke mode writes to `out/smoke` and checks two things.

- Isolation Forest and One-Class SVM, logs, fold 1, run at the published Optuna budget
  (10 trials). Precision, recall, F1, AUC-ROC and PR-AUC must each land within 0.02 of the
  archived per-fold row. In practice they match to four decimals (see below).
- DAGMM, logs, fold 1, run at a reduced budget (2 Optuna trials instead of 25, epochs capped
  at 30). It is checked for presence and finite metrics and passed through the FDES checks.
  It is not compared numerically, because a reduced search can't be expected to land on the
  published number. The recorded venv run gave F1 0.793 and AUC-ROC 0.908. The container run
  on the same machine gave F1 0.806 and AUC-ROC 0.908 (recorded under
  `runs/2026-08-29-smoke-m4pro/docker/`). The archived 25-trial values are 0.877
  and 0.939. The two classical rows were identical to four decimals in both environments.

The recorded smoke run (`runs/2026-08-29-smoke-m4pro/`) matched both classical rows to four
decimals. Its `fdes_checks.csv` also shows the section 8b guard firing. One-Class SVM on logs
scores F1 0.565 against a predict-all floor of 0.566 with recall 0.975 and is marked EXCLUDE.
Isolation Forest (F1 0.580, recall 0.742) and DAGMM (F1 0.793, AUC 0.908) PASS.

Full mode writes to `out/full` and checks three things.

- Per (model, signal) mean F1 and mean AUC-ROC against Table 4. Classical models must be
  within 0.02, deep models within max(0.05, 2 x published std). The deep models were trained
  on GPUs for the article and on CPU here, so bit-for-bit agreement is not claimed. The seed
  policy makes the run deterministic on a given machine, not across hardware and BLAS builds.
- The below-chance set must equal `{CNN1D_AE, LSTM_AE, LSTM_VAE}` (3 of 8, 37.5 percent).
- DAGMM must rank first in the Friedman test on each signal with p < 0.05.

`make verify-archive` is the FDES section 9 requirement ("archived tables regenerate exactly
from the archived raw scores"). It recomputes `metric_reconciliation.csv`, Table 4, the
below-chance set and the Friedman ranks from the archived scores and per-fold CSVs and
requires equality to three decimals. It runs in about two seconds.

## Runtimes and hardware

These times were measured on an Apple M4 Pro (12 cores, 24 GB), CPU only, Python 3.11.14,
torch 2.5.1, with the venv route.

| Target | Wall clock | Notes |
|---|---|---|
| `make fetch` | 34 s | 63 MB download plus md5 and unzip |
| `make smoke` | 150.4 s (2.5 min) | logs signal, fold 1. The two classical models take 10.6 s, the reduced-budget DAGMM the rest |
| `make docker-smoke` (smoke inside the container) | 238.7 s (4.0 min) | same machine, arm64 image, verify PASS, record in `runs/2026-08-29-smoke-m4pro/docker/` |
| `make verify` | under 2 s | |
| `make verify-archive` | 2 s | |
| `make pilot` | 2 s | example Isolation Forest plugin |
| `make check` | under 2 s | sample CSVs in `examples/`, one week at five minute buckets |
| `make test` | 6 s | 40 unit tests for the check path |
| `make reproduce` | not run in this repository yet | see the estimate below |

The full run was not executed here. The artifact records 41.8 hours of training time
(Optuna search plus final fit, summed over all 120 model-signal-fold cells) on the article's
SageMaker instances, with the deep models on NVIDIA A10G GPUs. The metrics signal (532k rows,
64 features) accounts for 27.1 of those hours.

On a laptop CPU the classical models finish in minutes per signal, and the deep models run
several times slower than on the GPU. `make reproduce` prints a working estimate of 2 to 5 days
on an 8-core CPU for the whole grid. Use `FOLDS=` and `SIGNALS=` to spread it across machines.
This estimate is an extrapolation from the smoke timing and the archived GPU times, not a
measurement.

## Seed policy

The wrapper seeds Python `random`, NumPy and PyTorch with 42 + fold id (fold 1 uses 43,
fold 5 uses 47) before each fold's `train_signal()` call. Inside the artifact, Optuna's TPE
sampler is seeded with 42, Isolation Forest uses `random_state=42`, the One-Class SVM subsample
uses `RandomState(42)`, and the baseline (normal-only) split uses `RandomState(42 + fold_id)`,
all unchanged.

Folds are partitioned by fault-injection repetition, never by random window sampling. The test
repetitions are {1,2}, {3,4}, {5,6}, {7,8} and {9,10}. The validation repetition is 10, or 8
for fold 5. Every run writes its seed policy, package versions and hardware to
`out/<mode>/run_manifest.json`.

## FDES v1.0.0-draft sections implemented

| Check | Spec section | Where |
|---|---|---|
| Predict-all baseline F1 = 2p/(1+p) reported next to every operating-point score | 2, 7 | `fdes/checks.py::predict_all_f1`, `tables/fdes_checks.csv` |
| Threshold-independent metrics from both families (AUC-ROC, PR-AUC) with random references (0.5, p) | 6, 7 | `fdes/checks.py::check_row` |
| Range-based metrics (VUS-PR, VUS-ROC) from the raw score vectors | 6 | `fdes/vus.py`, `tables/vus.csv`, pilot report |
| Exclusion when AUC-ROC is at or below the random reference | 8a | `sec8a_auc_at_or_below_random`, model-level count in `below_chance.json` |
| Flag-everything guard: F1 within 5 percent of the floor, above or below it, with recall at or above 0.95 | 8b | `fdes/checks.py::flag_everything`, `sec8b_flag_everything` |
| Degenerate-detector rule from the artifact's Table 12 support code (AUC <= 0.55 and F1 >= 0.95 x floor, `code/analysis/score_based_analyses.py`) | 8 | `degenerate_table12_rule`, `metric_reconciliation_from_raw_scores` |
| Folds by repetition, threshold on a disjoint validation repetition, raw scores retained | 5 (steps 1 to 3) | artifact `training.py`, and `fdes/protocol.py` for pilots |
| Prevalence re-scoring at 1, 5, 10, 20 percent | 5 (step 5) | artifact `code/analysis/prevalence_sensitivity.py` on `results/raw_scores/` |
| Episode-level detection rate. Step 6's detection-latency distribution is not implemented, here or in the artifact | 5 (step 6, in part) | artifact `training.py::evaluate_episode_detection` (`results/episode_results.csv`) |
| Published seed policy, per-fold results, regeneration from archived scores | 9 | this README, `expected/model_results_per_fold.csv`, `make verify-archive` |
| Sections 2, 6, 7 and 8 applied to an operator's own alert or score CSVs, with an explicit list of what the input could not support | 2, 6, 7, 8 | `fdes/byod.py`, `make check` |

The 5 percent margin in the section 8b check is this package's choice of "the evaluator's
stated margin". It is two sided, so F1 has to sit near the floor from either direction, and
a detector well above the floor is not in the flag-everything regime however high its
recall gets. Change `FLOOR_MARGIN` in `fdes/checks.py` to use your own margin.

`fdes/vus.py` is a port of the VUS reference implementation from TSB-AD
(https://github.com/TheDatumOrg/TSB-AD, Apache-2.0), the implementation of Paparrizos et
al., PVLDB 15(11), 2022, https://doi.org/10.14778/3551793.3551830, and matches it to float
precision on the archived score vectors. The buffer defaults to the median labeled
anomaly-segment length capped at 100 windows, the reference implementation's own
`get_metrics` default, and the value used is reported in every output row. `tables/vus.csv`
is computed from `results/raw_scores/`, which includes cooldown windows, so its values are
cooldown-included. The pilot report's VUS row is computed on the same cooldown-excluded
vector as the other pilot metrics.

## Run against your own detector

The pilot path evaluates any detector under the FDES procedure on the archived telemetry
(or on your own feature table) and returns a verdict.

1. Subclass `detectors.base.Detector` in a module on `PYTHONPATH`.

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

2. Run it on one signal and fold. Repeat for the folds you want.

   ```bash
   python bin/reproduce.py pilot --detector my_module:MyDetector --signal metrics --fold 1
   ```

   Exit code 0 means PASS, 2 means EXCLUDE under section 8. The report
   (`out/pilot/<name>_<signal>_fold<k>/pilot_report.md`) prints every check with its reference
   value. `pilot_result.json` has the numbers and the fold assignment. The raw test scores and
   labels are saved as `.npy` for the prevalence re-scoring step.

3. To use your own telemetry, pass `--features path/to/features.parquet`. The table must
   contain `label` (0 or 1 per window), `rep` (repetition id 1 to 10, used for the fold split),
   `phase` (`baseline` for normal-only collection, anything else for the fault campaign),
   `timestamp` and `fault_type` (used for cooldown marking and per-fault reporting), plus
   numeric feature columns. Column names `service, run_id, testbed, count, fold` are treated
   as metadata and ignored as features.

The shipped example, `detectors/example_isolation_forest.py`, uses the hyperparameters Optuna
selected for the article's Isolation Forest on logs fold 1. `make pilot` runs it and reproduces
that archived row exactly (precision 0.4755, recall 0.7420, F1 0.5796, AUC-ROC 0.6355,
PR-AUC 0.5321 on 10,449 cooldown-excluded windows). That is the self-consistency check that
the pilot protocol scores a detector the way the article did.

## Check your own data

The pilot path above needs the archived feature tables and a detector object that returns
one score per window. Most operators have neither. Anomaly detection usually arrives
pre-installed inside a vendor product. Datadog ships Watchdog and `anomalies()` monitors,
Splunk ships ITSI adaptive thresholding and MLTK, Dynatrace ships Davis. What comes out of
those is not a score column. It is a list of alert windows, and the ground truth is a list
of incident windows from a postmortem or an incident tracker.

`make check` takes that shape. It buckets a timeline, marks every bucket alerted or not and
truly anomalous or not, and runs the same FDES section 2, 7 and 8 checks the article used.
It needs no Zenodo artifact, no detector plugin and no GPU. CSV in, verdict out.

### Thirty seconds, on the sample data

```bash
make check                                    # the good sample detector, verdict PASS
make check ALERTS=examples/alerts_useless.csv # the useless one, verdict EXCLUDE
```

`examples/` ships seven synthetic CSVs (one ground truth and six detectors) plus the
script that generates them
(`examples/make_examples.py`). The scenario is one week with five incidents covering about
5 percent of it. Each file uses a different timestamp format on purpose, so running them all
shows what the tool says it assumed about each.

| File | What it is | Verdict | Exit code |
|---|---|---|---|
| `incidents.csv` | the ground truth, five incident windows | | |
| `alerts_good.csv` | catches four incidents of five and fires three times when nothing was wrong | PASS | 0 |
| `alerts_useless.csv` | a static threshold on a metric that swings with the nightly batch window, so it is a clock and not a detector | EXCLUDE | 2 |
| `alerts_everything.csv` | one window over the whole range | EXCLUDE | 2 |
| `scores_good.csv` | an anomaly score that really does lift during incidents | PASS | 0 |
| `scores_useless.csv` | a plausible-looking score with no relationship to the incidents | EXCLUDE | 2 |
| `scores_flat.csv` | a model that collapsed and emits one constant number | EXCLUDE | 2 |

`alerts_useless.csv` is the one worth reading. It scores F1 0.051, which looks like a
number until it is put next to the predict-all floor of 0.096 at that prevalence. Flagging
every single bucket would have scored twice as well. `scores_useless.csv` is the same trap
one level up. Its F1 of 0.097 clears the floor by a hair, and then AUC-ROC 0.358 shows it
ranks the buckets worse than a coin.

### On your own data

Two modes. Both take a ground-truth CSV of incident windows.

**Alerts mode.** A CSV of alert windows, a CSV of incident windows, a time range and a
bucket size. This is the mode for a vendor product.

```bash
python bin/reproduce.py check \
  --alerts my_alerts.csv \
  --incidents my_incidents.csv \
  --bucket 5m \
  --from 2026-03-01T00:00:00Z \
  --to 2026-03-08T00:00:00Z
```

**Scores mode.** A CSV with a timestamp column and a score column, plus the same incident
windows. This is the richer case and it is the only one that supports the rank metrics.

```bash
python bin/reproduce.py check \
  --scores my_scores.csv \
  --incidents my_incidents.csv \
  --bucket 5m \
  --from 2026-03-01T00:00:00Z \
  --to 2026-03-08T00:00:00Z \
  --threshold 0.82
```

The same through `make`, which is handy when you keep re-running it:

```bash
make check ALERTS=my_alerts.csv INCIDENTS=my_incidents.csv BUCKET=5m \
  FROM=2026-03-01T00:00:00Z TO=2026-03-08T00:00:00Z
make check SCORES=my_scores.csv INCIDENTS=my_incidents.csv BUCKET=5m \
  FROM=2026-03-01T00:00:00Z TO=2026-03-08T00:00:00Z THRESHOLD=0.82
```

Exit code 0 means PASS, 2 means EXCLUDE and 3 means INSUFFICIENT. The run writes
`out/check/<name>/check_result.json` and `check_report.md`.

### The three verdicts

`EXCLUDE` and `INSUFFICIENT` are opposite messages. One says fix your detector. The other
says fix your input.

| Verdict | Exit code | What it means | What to do |
|---|---|---|---|
| `PASS` | 0 | the detector cleared every check that could be evaluated | read the report for which checks those were |
| `EXCLUDE` | 2 | the input supported a verdict and the detector failed a check | the detector is not worth deploying as it stands |
| `INSUFFICIENT` | 3 | the input could not support a verdict either way | fix the CSVs or the range, then run it again |

A run is `INSUFFICIENT` when any of these hold.

- Prevalence is zero. No bucket in the range falls inside an incident window.
- Every incident window falls entirely outside the range.
- The incident CSV has no rows.
- Every bucket falls inside an incident window, so the ground truth has one class.
- Every alert window falls entirely outside the range. Rows that all miss the range point
  at a wrong `--from` and `--to`. An alert CSV with no rows at all is different. That is a
  detector that never fired, it is a real result, and it gets a real verdict.

On an `INSUFFICIENT` run no check is applied and every check reports `not evaluable`. None
of them report `pass`. This matters more than it looks. At prevalence zero the predict-all
floor is also zero, so an F1 of zero sits exactly on the floor. The lift guard, the
flag-everything guard and the degenerate-output guard would all have read `pass`, and the
floor row would have read `F1 minus floor = +0.000`. Three pass marks and a plus sign on a
run holding no information is the exact failure this specification exists to criticise. The
reason the run could not be evaluated is printed at the top, next to the verdict.

### Overlapping alert windows

Windows are unioned into one alerted mask, because the question each bucket answers is
"was this bucket alerted", and two concurrent alerts on the same bucket are still one
alerted bucket. Concurrent alerts are normal on real data. Latency stories on different
services overlap constantly, so a row count can overstate the alert time badly. On one
Datadog Watchdog export, 156 stories merged into 95 distinct stretches and about a quarter
of the alert time sat under another story.

The union is kept and the cost of it is reported. Every run states the input row count, the
number of distinct stretches after merging, and how many buckets were absorbed. When the
absorbed share reaches 10 percent the report says so at the top as well, so the row count is
not read as a measure of covered time. The same counts are in `check_result.json` under
`coverage`.

### The CSV format

Alert and incident CSVs need a start column and an end column. Score CSVs need a timestamp
column and a score column. Column names are matched case insensitively against the names
real exports use, so `start` / `start_time` / `started_at` / `triggered_at` / `from` all
work for a start, `end` / `end_time` / `resolved_at` / `to` for an end, `timestamp` /
`time` / `ts` / `_time` for a time, and `score` / `anomaly_score` / `value` / `deviation`
for a score. Anything else, name it with `--start-col`, `--end-col`, `--timestamp-col` or
`--score-col`. Extra columns are ignored, except that a `severity` or `priority` column is
counted in the report.

An alerts CSV with a start but no end is read as point events, one bucket each. An alerts
CSV with a header and no rows is read as "this detector never fired", which is a real
result and gets a real verdict.

Timestamps can be ISO 8601 or epoch seconds. Both of these are fine, and so is mixing the
two across files, just not inside one column.

```
start,end
2026-03-01T14:20:00Z,2026-03-01T15:15:00Z
2026-03-02T15:05:00+00:00,2026-03-02T17:15:00+00:00
```

A timestamp with no timezone is read as UTC and the report says so. If your whole export
is in one local zone that is harmless, because the same offset applies to every file. What
is not harmless is mixing timezone-aware and naive rows inside one file, and the report
calls that out. Epoch milliseconds are refused with a message telling you to divide by
1000, rather than silently read as the year 58000. Sub-second precision is floored away.

### The time range, and why it is not guessed

Alerts and incidents say when something happened. They do not say when you were watching.
So `check` refuses to infer the evaluation range from them and asks for `--from` and
`--to`, printing the span of your data so you can copy it.

`--infer-range` uses that span anyway. It makes the range as tight as the events allow,
which throws away every quiet period outside them, and that inflates prevalence and
flatters precision. It is there for a quick look, and the report says loudly that it was
used.

### What it computes

| Check | Spec section | Alerts mode | Scores mode |
|---|---|---|---|
| Prevalence p from the ground-truth buckets | 2 | yes | yes |
| Precision, recall, F1 at the operating point | 6 | yes | yes |
| Predict-all baseline F1 = 2p/(1+p), and F1 minus it | 2, 7 | yes | yes |
| Lift over the predict-all baseline | 7 | yes | yes |
| Flag-everything guard | 8b | yes | yes |
| Degenerate output guard (alerts on all, alerts on none, one near-constant score) | 8 | yes | yes |
| AUC-ROC against its 0.5 reference | 6, 7, 8a | **no** | yes |
| PR-AUC against its p reference | 6, 7 | **no** | yes |
| VUS-PR and VUS-ROC | 6 | **no** | yes, but only when every bucket in the range holds a score sample |
| Degenerate rule from the article's Table 12 | 8 | **no** | yes |

### What alerts mode refuses to compute, and why

**A threshold-independent rank metric cannot be computed from binary alerts.** A rank
metric sweeps the threshold and measures how well the scores order the buckets. An alert
window is a decision, not a score, so the threshold has already been applied and there is
no ordering left to sweep.

Passing a 0/1 vector to an AUC function does return a number. That number is a rescaling
of the balanced accuracy at the one operating point you already have. It is a different
quantity from a score-based AUC-ROC, and printing it in the same column as one would be
the exact mistake this specification exists to stop. So alerts mode does not print it at
all. The report has a "What this run could not compute" section that names AUC-ROC, PR-AUC,
VUS-PR and VUS-ROC and gives the reason in full.

Section 8a is the exclusion that fires when a threshold-independent score sits at or below
its random reference. With no rank metric there is nothing to compare against 0.5, so
section 8a cannot fire in alerts mode. The report says that too. **A PASS in alerts mode
rests on fewer checks than a PASS in scores mode, and it is not evidence that section 8a
would have been passed.** To get the rest, export the underlying anomaly score or deviation
series and use scores mode.

The same refusal applies in scores mode when the score column holds two or fewer distinct
values. A 0/1 column is alerts in disguise.

### The threshold in scores mode

FDES section 5 step 2 wants the threshold picked on a validation split that is disjoint
from the test set. One CSV cannot give that. So:

- pass `--threshold` with the value your monitor actually uses, and you get your real
  operating point,
- or pass nothing, and the threshold is tuned in sample to maximise F1. That is an
  optimistic upper bound, not a held-out estimate, and the report says so every time.

`--aggregate max` (the default) or `--aggregate mean` reduces several score samples inside
one bucket. Buckets with no score sample at all are dropped from the evaluation rather than
treated as low-scoring, and the report counts them. Dropping them puts holes in the time
axis, so VUS is skipped whenever any bucket is empty. VUS is range based and would score a
timeline that never existed. AUC-ROC and PR-AUC ignore order and are reported either way.

### A worked example

```bash
$ make check ALERTS=examples/alerts_useless.csv
```

```
# FDES v1.0.0-draft check report: your own data (alerts mode)

Verdict: **EXCLUDE**

Timeline 2026-03-01T00:00:00Z to 2026-03-08T00:00:00Z, 2016 buckets of 300 s.
2016 buckets were evaluated, of which 102 fall inside an incident window.

| Check | Section | Value | Reference | Result |
|---|---|---|---|---|
| Prevalence | 2 | p = 0.0506 (102 of 2016 buckets) | | reported |
| Predict-all F1 floor | 2, 7 | F1 = 0.051 | floor = 0.096 (p = 0.0506) | F1 minus floor = -0.045 |
| Lift over predict-all | 7 (this tool's rule) | F1 / floor = 0.53 | > 1.0 | EXCLUDE |
| Threshold-independent (ROC) | 6, 8a | NOT COMPUTED | 0.5 | see below |
| Threshold-independent (PR) | 6, 7 | NOT COMPUTED | p | see below |
| Range-based (VUS) | 6 | NOT COMPUTED | | see below |
| Flag-everything guard | 8b | recall = 0.078, alerted rate = 0.104 | F1 within 5% of floor and recall >= 0.95 | pass |
| Degenerate output guard | 8 | alerted rate = 0.104, distinct scores = n/a | alerts on all, alerts on none, or one near-constant score | pass |

## Operating point

Precision 0.038, recall 0.078, F1 0.051. True positives 8, false positives 202,
false negatives 94, true negatives 1712.

## What this run could not compute

**Every threshold-independent rank metric (AUC-ROC, PR-AUC, VUS-PR, VUS-ROC)**

Alerts mode gets binary alert windows. An alert window is a decision, not a score, so it
is already thresholded. A rank metric sweeps the threshold and measures how well the
scores order the buckets. With the threshold already applied there is no ordering left to
sweep. [...]

## Why the verdict is EXCLUDE

1. F1 0.0513 is at or below the predict-all floor 0.0963, so flagging every bucket would
   have scored the same or better.

## What was assumed

1. examples/alerts_useless.csv: timestamps read as ISO 8601, no row carried a timezone,
   so every timestamp was read as UTC.
2. examples/incidents.csv: timestamps read as ISO 8601, all rows carried a timezone.
3. Sub-second precision was floored away. The bucket is 300 s, so this only matters if
   your events are shorter than a second.
```

The full report also lists the input files, their row counts, their severity distribution
and what merging overlapping windows absorbed. `check_result.json` next to it carries every
number, the bucketing, the per-check `check_status`, the window coverage counts, and the
machine-readable `not_computed` list.

## Layout

```
bin/reproduce.py        single entry point (fetch, smoke, reproduce, verify, verify-archive, estimate, pilot, check)
bin/build_expected.py   regenerates expected/ from the fetched artifact
fdes/checks.py          FDES section 2, 6, 7, 8 checks
fdes/protocol.py        FDES section 5 procedure for pilots (reuses the artifact's split, cooldown, threshold and metric code)
fdes/tables.py          Table 4, 5, 7, 8, below-chance, Friedman, VUS
fdes/vus.py             VUS-PR and VUS-ROC, ported from the TSB-AD reference implementation
fdes/byod.py            the bring-your-own-data path behind `make check`
detectors/base.py       plugin interface; detectors/example_isolation_forest.py
examples/               sample alert, incident and score CSVs plus the script that makes them
tests/                  unit tests for the check path (`make test`, no Zenodo needed)
expected/               archived values verify compares against
runs/                   recorded runs: manifest, verify reports, tables (evidence, not inputs)
Dockerfile              python:3.11-slim@sha256:1042b6... + CPU torch 2.5.1 + exact pins
data/, out/             created by fetch and the runs; git-ignored
```

## License

The wrapper code and packaging in this repository (`bin/`, `fdes/`, `detectors/`,
`examples/`, `tests/`, `Makefile`, `Dockerfile`, `requirements.txt`, `CITATION.cff` and
this README) are Apache-2.0. The Zenodo
artifact fetched into `data/` and the derived tables in `expected/` are CC-BY-4.0, attribution Mateen Ali Anjum, DOI 10.5281/zenodo.22078287. See `LICENSE`.

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

@software{anjum2026otelreproduction,
  author    = {Anjum, Mateen Ali},
  title     = {Reproduction package for: Evaluating {ML}-Based Anomaly Detection on Unified
               {OpenTelemetry} Telemetry},
  version   = {1.1.0},
  publisher = {Zenodo},
  year      = {2026},
  doi       = {10.5281/zenodo.22185619}
}
```

This reproduction package is archived at Zenodo, DOI
[10.5281/zenodo.22185619](https://doi.org/10.5281/zenodo.22185619) (v1.1.0, concept DOI
[10.5281/zenodo.22170201](https://doi.org/10.5281/zenodo.22170201) resolves to the latest version).

The specification is the Failure Detection Evaluation Specification v1.0.0-draft,
https://github.com/mateenali66/failure-detection-evaluation-spec. `CITATION.cff` holds the
machine-readable metadata.

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
| Flag-everything guard on the alerted rate: alerting on at least half the wall-clock time at four times prevalence, or on at least a fifth of it at ten times prevalence | 8b | `fdes/checks.py::alert_rate_saturated`, `alert_rate_far_above_prevalence`, `make check` only |
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

Section 8b is read a second time on the alerted rate, because the F1 form compares one
number against another number computed from the same prevalence and a detector can sit a
rounding-level distance outside the margin. See "The alerted rate against prevalence"
below for the rule, the thresholds and the case that motivated it. That reading is applied
by `make check` and not by `make pilot`, because the archived per-fold table has no column
for it and `tables/fdes_checks.csv` is compared byte for byte against the recorded run.
Applying it to the 120 archived folds changes no verdict there, so the two paths agree on
the published data.

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

   Exit code 0 means PASS, 2 means EXCLUDE under section 8 and 3 means INSUFFICIENT. A
   failure exits 1 and no verdict uses that code, so an exit of 2 is a rejected detector
   and not a crash. The report (`out/pilot/<name>_<signal>_fold<k>/pilot_report.md`) prints every check with its
   reference value. `pilot_result.json` has the numbers, the fold assignment and the
   per-check `check_status`. The raw test scores and labels are saved as `.npy` for the
   prevalence re-scoring step.

   A fold whose evaluated windows carry one class only is `INSUFFICIENT`. AUC-ROC is
   undefined there, precision, recall and F1 carry no information, and no check can be
   applied. Each check reports `not evaluable` and the report says why, next to the verdict.
   No check reports a pass and the run is never `PASS`. This cannot happen on the archived
   folds, which all carry both classes, but it can happen on a feature table you supply with
   `--features`.

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

Exit code 0 means PASS, 2 means EXCLUDE, 3 means INSUFFICIENT and 4 means UNSTABLE. The run
writes `out/check/<name>/check_result.json` and `check_report.md`.

### Where the incident windows came from

The check compares one set of windows against another. It cannot tell whether the two came
from the same place. If your incident windows were built from the alerts you are scoring, or
from anything downstream of the detector under test, the result is circular and means
nothing. It will usually look excellent.

This is easy to do by accident. Deriving incident windows by clustering the detector's own
alerts gives near-perfect recall and a high lift, because every incident was defined by an
alert. Grouping tickets opened by the monitoring system under test does the same thing one
step removed. So does a tracker whose incidents are auto-created from those alerts.

Ground truth has to come from somewhere the detector cannot reach. Human-declared incidents,
customer-reported outages, postmortems, or a monitoring stack with no shared inputs. If you
must derive windows from alerts, split by provenance first. Score the detector's alerts only
against windows built from sources it does not feed.

State where your windows came from when you report a result. A verdict without that is not
interpretable, whichever way it went.

The tool cannot check any of this, so every report says so in the assumption list. That line
is the only place many readers will meet it.

### Getting alert data out of a real vendor

This is the hardest part of using the tool and it has nothing to do with the tool. The
export is where the result is won or lost, so what follows is what one real export took.

**Datadog Watchdog.** Watchdog is the case that breaks every reasonable assumption.

- There is no monitor behind it. Watchdog is not a monitor you can list, and it is not in
  the Datadog MCP surface either, so anything that walks monitors will report that you have
  no Watchdog data. You do.
- Its output is Events, not Logs. Searching Logs first returns nothing and reads as a
  clean negative. It is not one. Go to Events.
- Use the Events API v2 with `filter[query]=source:watchdog`. That gets you the raw
  stream.
- Watchdog emits one event per story update, not one event per window. To get windows,
  group the events on the `story_key` tag and take the minimum and maximum event timestamp
  in each group. That gives you a start and an end per story.
- **Those ends are not resolution times.** The maximum is the last update Watchdog sent,
  which is usually before the story actually ended and sometimes long before. You invented
  those end times at export, and this tool will treat them as exact, because a CSV carries
  no uncertainty. Every false negative charged near the end of a window may be an artefact
  of that reconstruction.
- Pagination truncates silently. One run of ours stopped at 6000 events and said nothing
  about it. The tool only ever sees your CSV, so a short export looks exactly like a quiet
  week. Page until the cursor is exhausted and count the rows yourself before you trust the
  file.
- Check `has_notification` before you measure anything. A Watchdog story that never paged
  a human is a different object from a monitor that woke somebody at 3am. Measuring the two
  together answers a question nobody asked. Decide which one you are evaluating and filter
  to it.

**Incident trackers in general.** A close time in PagerDuty, Jira or ServiceNow is ticket
hygiene, not impact. It records when somebody clicked resolve. Tickets get closed at the
end of the shift, on Monday morning, or never. Incident windows built from close times are
longer than the incident was, sometimes by orders of magnitude, and the tool reads them as
exact ground truth.

Where you can, build the incident windows from when alerting started and stopped rather
than from the ticket. That is usually much closer to the truth, it is recoverable from the
same alerting system you are evaluating (use a different signal from the one under test, or
the on-call paging record), and it does not depend on anybody remembering to close
anything. When you cannot, read the forgotten-ticket notice below, because the tool now
looks for the rows this produces.

### Incident rows nobody closed

Two forgotten tickets destroyed one real result. The export held 23 PagerDuty incidents
over a 35.6 day window. Two of them had been sitting open for 13 days and 12 days and
accounted for 601 of the 620 total incident-hours. The remaining 21 incidents held 19 hours
between them. Prevalence came out at 0.383 instead of 0.020, a 19-fold move, and the
verdict turned over. Nobody was in a 13 day outage. Somebody forgot to close a ticket.

The tool said nothing. The assumption text warned that tracker times are imprecise, but
that warning is about minutes of slop at the window edges, not about one row spanning a
third of the observation window. Every incident tracker on earth has forgotten-open
tickets, so this hits almost everyone who runs the tool.

An incident row is now called implausibly long when **both** of these hold.

1. It covers at least 10 percent of the observation range. A window spanning a tenth of
   everything you watched is not an incident, it is a state.
2. It runs at least 10 times the median incident in the same file. It has to be an outlier
   against its own population, not just large.

Both conditions are needed and each one protects a case the other gets wrong. Without the
first, ordinary spread is condemned: in a file where the median row is ten seconds, a five
minute blip is 30 times the median and perfectly normal. Without the second, a real outage
in a short observation window is condemned: four hours of a single day is 17 percent of the
range and there is nothing wrong with it. The guard also needs at least three rows in
range, because a median of one or two rows is not a median.

When it fires, the report says so next to the verdict, names how many rows, how long they
are, what share of the observation range and what multiple of the median that is, and what
share of all incident time they own. It says plainly that a small number of rows own most
of the positive class and that prevalence, the floor and the verdict rest on them. It also
prints what prevalence would have been without them, so the size of the effect is on the
page rather than left as an exercise.

**Nothing is dropped.** Which rows are real is your call and the tool does not get to make
it. The rows are listed in `check_result.json` under `incident_concentration`, with their
start, end, length, share of range and share of incident time, so you can go and look them
up in the tracker.

The general form of the same problem is reported too, whether or not any single row looks
broken. When the longest tenth of the incident rows owns half or more of all incident time,
the report says the ground truth is lopsided and names the share. A file like that is not
necessarily wrong, and correcting one or two end times would still move the result.
`LONG_INCIDENT_RANGE_SHARE`, `LONG_INCIDENT_MEDIAN_MULTIPLE` and `CONCENTRATION_NOTICE` in
`fdes/byod.py` set the three thresholds.

### The bucket size chooses the verdict

On the same real export the verdict was `EXCLUDE` at 1m and 5m and `PASS` at 15m, 30m, 1h
and 2h. The lift climbed monotonically: 0.82, 0.90, 1.05, 1.21, 1.25, 1.28. The detector
never changed. The cause is mechanical. A coarse bucket lets one short alert cover an
entire incident bucket for free, so recall rises with the bucket size while precision is
barely charged for it. The operator picks the bucket, and by picking the bucket they pick
the answer.

So alerts mode does not hand back one bucket's answer as if it were settled. It runs the
whole check at 1m, 5m, 15m and 1h, plus whatever `--bucket` you passed, and reports the
verdict at every one of them.

- When every bucket agrees, the verdict stands and the report says the sweep was run and
  what it found, so a reader knows the question was asked. The table sits under the checks.
- When they disagree, the verdict is `UNSTABLE`, exit code 4. The report leads with the
  table of verdicts, says which buckets gave which answer, and says that the single-bucket
  numbers printed below it are reported for the record and are not the answer. When the
  lift rises at every step of the ladder, it names that as the signature of the mechanism
  above rather than of a detector that works at one time scale.

`UNSTABLE` is not a softer `EXCLUDE`. It says this input cannot settle the question on its
own. To settle it, pick the bucket size from the time scale your responders actually work
at and be able to say why you picked it, or treat the result as undecided. Only rows that
gave `PASS` or `EXCLUDE` are compared. A bucket size that comes out `INSUFFICIENT` is
listed and left out of the comparison, because it says there was nothing to measure there,
not that the answer flipped.

`--no-sweep` turns it off and gives you the single-bucket verdict and its exit code back.
The sweep is four extra runs of arithmetic on the same arrays, so it costs nothing worth
saving. `SWEEP_BUCKETS` in `fdes/byod.py` sets the ladder. The sweep is alerts mode only:
in scores mode the operating point moves with the bucket as well as the bucket boundaries,
so a sweep there would change two things at once and answer neither.

### Scope, when the two files describe different systems

Watchdog covered 34 services in that same run. All 23 PagerDuty incidents came from a
single service. So most of the 156 alerts could not possibly have corresponded to any
incident in the file, and every one of them was scored as a false positive. The CSVs carry
no notion of scope, so nothing noticed, and precision, F1 and the verdict were all
measuring the mismatch rather than the detector.

Both window CSVs can now carry an optional service or scope column. `service`,
`service_name`, `scope`, `entity` and `component` are recognised by name, and `--scope-col`
and `--incident-scope-col` name anything else. `--incident-scope-col` falls back to
`--scope-col`, the same as the start and end overrides.

```bash
python bin/reproduce.py check \
  --alerts watchdog_export.csv --start-col triggered_at --end-col resolved_at \
  --scope-col service \
  --incidents pagerduty.csv --incident-scope-col service_name \
  --bucket 5m --from 2026-03-01T00:00:00Z --to 2026-04-05T00:00:00Z
```

When both files have one, the report gives the number of distinct scopes on each side, how
many they share, and what share of the alert rows fall on scopes that appear in no
incident. When at least half of the alert rows fall on unmatched scopes, the report says
next to the verdict that the two files may be describing different systems and that the
result is unreliable until they cover the same scope.

Scope strings are compared for equality, so two exports naming the same service differently
(`checkout` against `checkout-api`, or a Datadog tag against a PagerDuty service name) read
as no overlap at all. Normalise the names in the CSVs before you trust the share.

**An empty intersection is usually a naming difference.** A second real run carried 31
scopes from Datadog's `service` tag, which are application names like `is-api` and
`is-admin-mongodb`, against exactly 1 scope from PagerDuty's `service` field, which was
`InvoiceSimple-Alerts`. That is one catch-all routing destination, and a single catch-all
PagerDuty service is the normal setup. Both vendors legitimately call the field `service`
and they mean different things by it. Datadog means the application the signal came from.
PagerDuty means where the page was sent. The intersection is empty by construction and it
always will be.

Two things follow, and the report used to get both of them wrong. The unmatched share reads
100 percent whatever the detector did, so it is not evidence of a system mismatch. And the
remedy for a poor overlap, filtering both exports to the scopes they share, leaves an empty
file. So when the intersection is empty and one side carries exactly one distinct scope, the
report says the two files are using two naming schemes rather than describing two systems,
withholds the filter remedy, and points at taking the incident scope from the alert payload
the incident was created from or from the incident title.

The threshold is one distinct scope, not a ratio. One value cannot be a partial list of
application names, so the reading is unambiguous. Two or three names against thirty is
suggestive of the same thing and it is not unambiguous, because two exports really can cover
two small estates that do not overlap. Those keep the mismatch reading. They lose only the
filter remedy, which cannot be followed on an empty intersection either way. A genuine
partial overlap, where at least one name appears on both sides, is untouched and still gets
the original wording and the original remedy. `SCOPE_NAMESPACE_MAX_SCOPES` in `fdes/byod.py`
sets the threshold, and `check_result.json` carries `empty_intersection`,
`namespace_mismatch`, `single_scope_side` and `single_scope_name` under `results.scope`.

**Scope is reported and never applied.** No row is filtered by it and no number moves
because of it, so the same two files give the same verdict with the column and without it.
Filtering both exports to the scopes they share is a decision about what you are measuring,
and it belongs to you.

When the columns are absent, the run behaves exactly as it did before, and the report says
in the assumption list that scope was not checked and what that risks: every alert is
scored against every incident whatever system it came from, alerts on services with no
incident in the file are counted as false positives and could not have been anything else,
and nothing in the numbers shows it. Scores mode always says this, because a score series
has one row per timestamp and no scope column to compare with.

### The four verdicts

`EXCLUDE` and `INSUFFICIENT` are opposite messages. One says fix your detector. The other
says fix your input. `UNSTABLE` says the input and the bucket size together cannot settle
it. The pilot path uses the first three verdicts and the same exit codes.

| Verdict | Exit code | What it means | What to do |
|---|---|---|---|
| `PASS` | 0 | the detector cleared every check that could be evaluated | read the report for which checks those were |
| | 1 | not a verdict. The command failed on a bad argument, an unreadable CSV or a missing file, and printed the reason to stderr | fix what the message names |
| `EXCLUDE` | 2 | the input supported a verdict and the detector failed a check | the detector is not worth deploying as it stands |
| `INSUFFICIENT` | 3 | the input could not support a verdict either way | fix the CSVs or the range, then run it again |
| `UNSTABLE` | 4 | the verdict changed across bucket sizes, so no single verdict is the result (alerts mode) | pick the bucket from the time scale your responders work at and say why, or treat it as undecided |

`--no-sweep` holds the verdict and the exit code at the bucket you passed. It does not hide
what the sweep found. A run that would have been `UNSTABLE` says so at the top of the report
and names the exit code it would have had. It used to skip the sweep entirely, so the same
data at the same bucket came back as `UNSTABLE` at exit 4 with the sweep and `PASS` at exit
0 without it, with nothing said either way.

A non-zero exit is not a crash here. Only 1 means the command failed, and no verdict uses
it, so continuous integration can branch on the code without reading the output. `make
verify` uses the same convention: 0 for a match and 1 for a mismatch. The same table is in
`python bin/reproduce.py check --help` and `pilot --help`.

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

### The alerted rate against prevalence

A detector that alerts on nearly all of the time is the failure this whole tool exists to
catch, and the F1 form of section 8b lets some of them through. That form asks whether F1
sits within 5 percent of the predict-all floor with recall at or above 0.95. Both numbers
are computed from the same prevalence, so the margin is narrow and a detector can miss it
by a rounding-level distance.

One real Splunk and PagerDuty export did exactly that. Prevalence 0.043, recall 1.00,
precision 0.045, alerted rate 0.945, F1 0.087 against a floor of 0.082. F1 sits 5.6 percent
above the floor, which is outside a 5 percent margin by six tenths of a percentage point,
so section 8b did not fire and the verdict was `PASS`. The detector had alerted on 94.5
percent of the week. At a 15 minute bucket the same detector alerted 95.4 percent of the
time and was correctly excluded, so the verdict turned on the bucket size.

The alerted rate against prevalence separates the two cases cleanly. A detector that works
alerts about as often as things are actually anomalous, so its alerted rate sits near
prevalence. A detector that flags everything alerts far more often than anything is wrong.

The rule is one line. **The guard fires when the alerted rate squared reaches twice
prevalence.** Since the alerted rate divided by prevalence is recall divided by precision,
that is the same as saying the alerted rate multiplied by its ratio to prevalence reaches 2.
You may alert often, or you may alert far more often than incidents occur. You may not do
both. `ALERT_RATE_PRODUCT` in `fdes/checks.py` sets the 2, and `alert_rate_bar` solves the
rule for the rate so a report can print the bar a detector had to stay under.

**Why a curve and not a threshold.** This started as one condition, then two, and both
shapes had the same defect. A threshold that is constant in prevalence has a flat region,
and inside that region the constant decides alone while the ratio never binds. The first
shape was flat below a prevalence of 0.125, because four times 0.125 is 0.5, and real
incident data almost never sits above 0.125. The two-path shape that replaced it narrowed
the flat regions rather than removing them, and two of them survived:

| prevalence | the bar under the two-path shape | ratio it allowed |
|---|---|---|
| at or below 0.02 | 0.20, an absolute floor | up to 200 |
| 0.02 to 0.05 | ten times prevalence | 10 |
| 0.05 to 0.125 | 0.50, an absolute floor | 4 to 10 |
| above 0.125 | four times prevalence | 4 |

At a prevalence of 0.002 a detector alerting on 19.89 percent of the timeline passed and one
alerting on 20.09 percent was excluded, and both were alerting about a hundred times more
often than anything was wrong. The 0.20 decided it and the ratio was irrelevant. The 0.05 to
0.125 stretch is worse, because that is ordinary prevalence rather than a corner.

Flat regions also make bucket sweeps read as unstable for a reason that has nothing to do
with the detector. At a kink, a rounding-level move in prevalence can carry the threshold
past a fixed alerted rate, so the same detector flips verdict between two bucket sizes.

The curve keeps the two anchors the threshold design had already chosen and joins them
instead of stepping between them. At a prevalence of 0.02 the bar is 0.20 and at 0.125 it is
0.50, exactly where the old floors sat. Everywhere else it interpolates, so the ratio always
binds and the bar always moves with prevalence.

| prevalence | the bar | ratio it allows |
|---|---|---|
| 0.001 | 0.045 | 45 |
| 0.010 | 0.141 | 14 |
| 0.020 | 0.200 | 10 |
| 0.050 | 0.316 | 6.3 |
| 0.125 | 0.500 | 4 |
| 0.300 | 0.775 | 2.6 |

**A perfect detector cannot be caught, and this is now provable rather than tested.** A
perfect detector has an alerted rate equal to prevalence, so it fires only when prevalence
squared reaches twice prevalence, meaning a prevalence of 2. That cannot happen. This
matters, because the one-sided version of section 8b this package used to ship excluded any
detector with recall at or above 0.95 whatever its F1, which wrongly excluded a detector
scoring F1 1.0 at recall 1.0. The two-sided margin fixed that and opened the hole above.
Reading the alerted rate closes the hole without reopening the original defect.

**Rare incidents are still protected.** At a prevalence of 0.001 the bar is 0.045, so a
detector alerting on 1 percent of the time with perfect recall is ten times prevalence and
well clear of it. What the curve no longer permits is a ratio of two hundred.

**The bar stays stricter than the baseline it exists to catch.** At full recall the guard
fires below a precision of the square root of half the prevalence, and that sits above
prevalence, which is the precision flagging everything achieves, for every prevalence under
0.5.

**When it fires on a detector that works.** This is the check most likely to catch a real
detector that finds incidents and simply costs too much to page on. A detector at a
prevalence of 0.02 alerting on 20 percent of the timeline with recall 1.000 scores 4.64
times the predict-all floor and is still excluded. That is the intended judgement, but a
bare `EXCLUDE` plus a sentence about alert volume reads as "this does not detect anything",
which is the opposite of what happened. So when the guard fires while the detector has lift
over the floor or high recall, the exclusion says so in words: excluded on alert volume, not
on failing to detect, with the lift and the recall named, and the trade handed back to the
reader as a judgement about their own on-call load.

The guard applies in both modes, and in scores mode it applies at whatever operating point
the threshold produces, so a score that ranks buckets well still cannot reach `PASS` while
its operating point flags most of the timeline.

**Known limit, and where the evidence actually sits.** Two production observability
exports have been run through this, one on Datadog and one on Splunk. On the Datadog export
the alerted rate ran 0.292 to 0.396 across four bucket sizes and two ground-truth variants,
with the ratio to prevalence running 11.7 to 14.8 once two multi-day stale incident rows
were removed. Every one of those now excludes on the curve, where several of them passed
under the earlier shapes. On the Splunk export the top rate was 0.945 and it excluded under
every shape.

Two datasets is not enough to move the constant again. It is enough to say where the bar
sits relative to the only real measurements there are, which is below all of them. The
report prints the ratio next to the verdict whenever it reaches four times prevalence and
the guard did not fire, so the number that carries the information is visible even when
nothing acts on it.

### There is no minimum recall

Nothing in this procedure sets a floor on recall, and a `PASS` can be carried by lift alone.
One real run gave four instantaneous alerts over four days. Recall 0.071, F1 0.130, lift
1.85, `PASS` at exit 0. The detector missed 93 percent of the incident time.

That is deliberate. A high-precision detector that fires rarely and is usually right is a
real thing worth keeping, and excluding it would be wrong. Bucket-level recall is also
pushed down by the export format as much as by the detector. A file of instantaneous alerts
cannot reach high bucket recall whatever the detector did, because one alert covers one
bucket while an incident covers many. A recall floor would mostly be measuring the export.

It is not left silent. When the verdict is a `PASS` and recall is under 0.5, the detector
missed more incident time than it caught, and the report says so next to the verdict along
with the precision and the floor that carried the verdict instead. `LOW_RECALL_NOTICE` in
`fdes/byod.py` sets the band, and `check_result.json` carries it as `low_recall`. If you
needed a detector to catch incidents rather than to confirm them, read that line before you
read the verdict.

### When the lift sits near the floor

The lift is F1 divided by the predict-all floor, and a lift near 1.0 means the detector
scores about what flagging every bucket would score. A verdict there turns on a
rounding-level difference, and the bucket size is a parameter you picked. On the export
above the lift was 1.17, 1.06, 1.05 and 1.02 at four bucket sizes. It never left the
neighbourhood of 1.0, and the verdict still flipped between `PASS` and `EXCLUDE` across
them.

When the lift lands within 20 percent of 1.0 either way, the report says so next to the
verdict and names the lift. Two bucket sizes that disagree mean the detector is sitting on
the floor, not that one of the runs is wrong. `NEAR_FLOOR_BAND` in `fdes/byod.py` sets the
band, and `check_result.json` carries the same flag as `near_floor`.

This notice is not enough on its own, which is why the bucket sweep above exists. A near
the floor notice says the margin is thin. The sweep says whether the answer actually moves.
In alerts mode the notice now points at the sweep result instead of asking you to go and
re-run the thing yourself.

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
counted in the report and a `service` or `scope` column is used for the scope check above.

The alerts CSV and the incidents CSV are separate exports and often disagree on header
names. A Watchdog export uses `triggered_at` and `resolved_at` while an incident tracker
uses `start` and `end`, so each file takes its own override. `--start-col` and `--end-col`
name the columns of the alerts CSV. `--incident-start-col` and `--incident-end-col` name
the columns of the incidents CSV, and when they are not given they fall back to
`--start-col` and `--end-col`, which is what a run that passes only those two already does.
In scores mode the incidents CSV is the only window file, so both pairs reach it under the
same fallback.

```bash
python bin/reproduce.py check \
  --alerts watchdog_export.csv --start-col triggered_at --end-col resolved_at \
  --incidents incident_tracker.csv --incident-start-col start --incident-end-col end \
  --bucket 5m --from 2026-03-01T00:00:00Z --to 2026-03-08T00:00:00Z
```

An alerts CSV with a start but no end is read as point events, one bucket each. That is a
large interpretive choice, so it is reported next to the verdict and not in the assumption
list below the table. The notice names the columns the file did have, which is what a
Splunk export needs: `_time`, `_indextime`, `earliest` and `latest` gives a recognised
start in `_time` and no recognised end, so every row becomes an instant even though
`latest` was right there. Name it with `--end-col latest` (or `--incident-end-col` for the
incidents file) and the windows come back. On one real export that difference alone turned
an `EXCLUDE` into a `PASS` at three bucket sizes.

The schema is read off the header, not off the rows, so a file with a header and no rows is
still described by the columns it declares. An alerts CSV with a header and no rows is read
as "this detector never fired", which is a real result and gets a real verdict.

A row that ends before it starts is refused, and so is a row that ends the same second it
starts. Both cover no time and both are export problems rather than measurements. The
message names the two columns and how many rows are affected. Point events are not caught
by this, because a file with no end column has no end to disagree with its start.

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
| Flag-everything guard on F1 and recall | 8b | yes | yes |
| Flag-everything guard on the alerted rate against prevalence, on either of two paths | 8b | yes | yes |
| The alerted rate over prevalence, reported when it is high and neither path fired | input quality | yes | yes |
| A notice when a `PASS` sits on recall under 0.5 | input quality | yes | yes |
| Degenerate output guard (alerts on all, alerts on none, one near-constant score) | 8 | yes | yes |
| Implausibly long and lopsided incident rows | input quality | yes | yes |
| Verdict at 1m, 5m, 15m and 1h, and whether they agree | input quality | yes, and `--no-sweep` still reports it | **no** |
| Scope overlap between the two files, and whether an empty one is a naming difference | input quality | yes, when both files carry a scope column | **no** |
| AUC-ROC against its 0.5 reference | 6, 7, 8a | **no** | yes |
| PR-AUC against its p reference | 6, 7 | **no** | yes |
| VUS-PR and VUS-ROC | 6 | **no** | yes, but only when every bucket in the range holds a score sample |
| Degenerate rule from the article's Table 12 | 8 | **no** | yes |

**Known limit in the degenerate output guard.** The near-constant test measures the spread
of the score column against its mean, so it reads a large constant offset as if the whole
column were constant. A series of `1e9 +/- 100` has a relative spread of 2e-7, under the
1e-6 tolerance, so it is called near-constant and excluded even though its ordering carries
real information. Rank metrics only read the ordering, so the offset should not matter. The
guard errs towards refusing rather than passing, and the report prints `score_spread` and
`distinct_scores` so the call can be checked. Centre the series, or subtract the offset, to
work around it. `CONSTANT_SPREAD_RATIO` in `fdes/byod.py` sets the tolerance.

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

Bucket sweep. The same two files were re-run at 1m, 5m, 15m, 1h and every bucket that
could be evaluated gave EXCLUDE, so the verdict does not turn on the bucket size you
picked. The table is under the checks.

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
| Alerted rate against prevalence | 8b | alerted rate = 0.104, p = 0.0506, ratio = 2.1x | rate >= 0.5 and >= 4 x p, or rate >= 0.2 and >= 10 x p | pass |
| Degenerate output guard | 8 | alerted rate = 0.104, distinct scores = n/a | alerts on all, alerts on none, or one near-constant score | pass |

## Operating point

Precision 0.038, recall 0.078, F1 0.051. True positives 8, false positives 202,
false negatives 94, true negatives 1712.

## The verdict at other bucket sizes

The same two files, re-bucketed. A coarse bucket lets one short alert cover a whole bucket
of incident time for free, so this table is the cheapest way to see whether the verdict is
a property of the detector or of the bucket size.

| Bucket | Buckets | Prevalence | Alerted rate | Recall | F1 | Floor | F1 / floor | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1m | 10080 | 0.0506 | 0.104 | 0.078 | 0.051 | 0.096 | 0.53 | EXCLUDE |
| 5m (yours) | 2016 | 0.0506 | 0.104 | 0.078 | 0.051 | 0.096 | 0.53 | EXCLUDE |
| 15m | 672 | 0.0536 | 0.104 | 0.111 | 0.075 | 0.102 | 0.74 | EXCLUDE |
| 1h | 168 | 0.0774 | 0.125 | 0.154 | 0.118 | 0.144 | 0.82 | EXCLUDE |

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
4. The incident windows were taken as exact ground truth. Postmortem and incident-tracker
   times usually are not. [...]
5. Provenance was not checked. This tool cannot tell whether your incident windows were
   derived from the alerts being scored. If they were, the result is circular. See "Where
   the incident windows came from".
6. A tracker close time is a record of when somebody closed a ticket, not a record of when
   impact stopped. A row left open over a weekend covers the weekend. [...]
7. Scope was not checked. Neither file carries a column this tool recognises as a service
   or scope, so name one with --scope-col and --incident-scope-col if you have it. [...]
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
tests/                  unit tests for the check and pilot paths (`make test`, no Zenodo needed)
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
  version   = {1.3.0},
  publisher = {Zenodo},
  year      = {2026},
  doi       = {10.5281/zenodo.22309755}
}
```

This reproduction package is archived at Zenodo, DOI
[10.5281/zenodo.22309755](https://doi.org/10.5281/zenodo.22309755) (v1.3.0, concept DOI
[10.5281/zenodo.22170201](https://doi.org/10.5281/zenodo.22170201) resolves to the latest version).

The specification is the Failure Detection Evaluation Specification v1.0.0-draft,
https://github.com/mateenali66/failure-detection-evaluation-spec. `CITATION.cff` holds the
machine-readable metadata.

# Reproduction package: ML anomaly detection on unified OpenTelemetry telemetry

This repository reproduces the IEEE Access article

> M. A. Anjum, "Evaluating ML-Based Anomaly Detection on Unified OpenTelemetry Telemetry:
> An Empirical Study Across Traces, Metrics, and Logs," IEEE Access, vol. 14,
> pp. 93576-93608, 2026. DOI [10.1109/ACCESS.2026.3705430](https://doi.org/10.1109/ACCESS.2026.3705430)

from its published Zenodo artifact (DOI [10.5281/zenodo.22078287](https://doi.org/10.5281/zenodo.22078287),
record v3.1.3). It runs in a pinned container with fixed seeds and documented expected output.

It is the reference implementation of the Failure Detection Evaluation Specification (FDES)
v1.0.0-draft ([github.com/mateenali66/failure-detection-evaluation-spec](https://github.com/mateenali66/failure-detection-evaluation-spec))
and the implementation listed under RFC 7942 in the Internet-Draft
[draft-anjum-nmop-anomaly-detection-evaluation](https://datatracker.ietf.org/doc/draft-anjum-nmop-anomaly-detection-evaluation/).
[How the two documents relate](#relationship-to-fdes-and-to-the-ietf-draft) explains the
difference. A plugin interface lets you put your own detector through the same procedure.

This file covers running the tool. [docs/METHOD.md](docs/METHOD.md) explains each check and
why it is built the way it is. `CHANGELOG.md` lists changes by version.

| You want to | Read |
|---|---|
| Reproduce the article's numbers | [Quick start](#quick-start), then [What `make verify` compares](#what-make-verify-compares) |
| Check your own monitoring data | [Check your own data](#check-your-own-data). Two CSVs, no download, about two minutes |
| Put your own detector through the procedure | [Run against your own detector](#run-against-your-own-detector) |

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
make test             # unit tests (no Zenodo download needed)
```

`make check` and `make test` need neither the Zenodo download nor a GPU. They need numpy,
pandas and scikit-learn from `requirements.txt`.

The container route needs no local Python. After `make docker-build`, every target runs
inside the image with `data/` and `out/` mounted from the checkout. `NO_DOCKER=1` runs a
target on the local Python instead.

```bash
make docker-build     # python:3.11-slim pinned by digest, CPU torch, exact pins
make docker-smoke     # fetch, smoke and verify inside the container
```

The full reproduction covers 8 models x 3 signals x 5 folds. Read the runtimes section
before starting it.

```bash
make reproduce                       # prints the estimate, then runs everything
make reproduce FOLDS=1               # or one fold at a time
make verify MODE=full
```

To split the grid across machines, run slices with `bin/reproduce.py reproduce --out
<dir> --folds ... --models ...`, each covering all three signals, and join them with
`python bin/reproduce.py merge --out out/full <dir> <dir> ...`. A merged run gives the same
numbers as one `make reproduce` (see [Seed policy and determinism](#seed-policy-and-determinism)).

`bin/reproduce.py` is the single entry point behind every target, and `python
bin/reproduce.py -h` lists the subcommands. No cloud account, S3 bucket or SageMaker role is
needed. The artifact's SageMaker orchestration (`run_pipeline.py`) is not used, and
`training.py` runs in its `--local` code path with CPU forced.

## What gets reproduced, and from what

`make fetch` downloads `otel-aiops-benchmark.zip` from Zenodo record 22078287 and checks it
against the md5 in `expected/zenodo_checksums.json` (`ef2cd3b74b17d7984e56fe09b10684eb`,
62,897,450 bytes, taken from the Zenodo API on 2026-08-29). The zip holds the processed
60-second feature tables for the three signals (`data/features/*.parquet`, 58 MB), the
article's per-fold results and raw anomaly scores, and the pipeline code.

The pipeline runs as archived, with one fix applied at load time (see [Known differences
from the article](#known-differences-from-the-article)). This repository adds the wrapper:
seeding, CPU forcing, budget control, table building, verification, the FDES checks and the
detector plugin interface.

| Output file (under `out/<mode>/`) | Article table or figure | Produced by |
|---|---|---|
| `results/model_results.csv` | per-fold rows behind Table 4 (cooldown excluded) | artifact `training.py` |
| `results/model_results_with_cooldown.csv` | per-fold rows with cooldown included, averaged by `make paper-tables` into the DAGMM rows of Table 11 and the AUC (incl.) column of Table 12 | artifact `training.py` |
| `tables/table4_single_signal.csv` | Table 4 (mean and std over folds), Figures 2 and 3 | `fdes/tables.py` |
| `tables/table5_fusion.csv` | Table 5 late fusion, Figures 4 and 5 (needs a run with 2+ signals) | `fdes/tables.py` from `results/fusion_results.csv` |
| `tables/table7_per_fault_recall.csv` | Table 7 per-fault recall, Figure 6 | `fdes/tables.py` from `results/per_fault_type_results.csv` |
| `tables/table8_training_time.csv` | Table 8, Figure 7 (times reflect your CPU) | `fdes/tables.py` |
| `tables/below_chance.json` | the three-of-eight (37.5 percent) figure, models with mean AUC-ROC below 0.5 on every signal (Section V-B, FDES section 8) | `fdes/checks.py` |
| `tables/fdes_checks.csv` | FDES sections 7 and 8 per (model, signal, fold), covering the predict-all floor, AUC vs 0.5, the flag-everything guard and the Table 12 degenerate rule | `fdes/checks.py` |
| `tables/friedman_ranking.csv` | Friedman ranks per signal (Section V-C, needs 3+ folds and fusion results) | artifact `significance_tests.py` |
| `results/optuna_results.csv` | Table 3 search spaces, selected hyperparameters | artifact `training.py` |
| `results/raw_scores/*.npy` | per-window scores and labels, the input to Tables 10 and 12 | artifact `training.py` |
| `out/pilot/<detector>_<signal>_fold<k>/pilot_report.md` | FDES verdict for your own detector | `fdes/protocol.py` |
| `out/check/<name>/check_report.md` | FDES verdict for your own alert or score CSVs | `fdes/byod.py` |

### The article's tables and figures

`make paper-tables` and `make figures` rebuild the article's tables and figures in the
article's layout from one results directory, `out/full/results` by default.
`RESULTS=archive` points them at the Zenodo results the article was built from.

```bash
make paper-tables RESULTS=archive   # seconds, no training
make figures RESULTS=archive
make paper-tables                   # after make reproduce
```

Tables are written to `<run>/paper/tables/` as CSV and LaTeX, figures to `<run>/paper/figures/`
as PDF and PNG. Every printed cell is compared with the article's values in `expected/paper/`,
each difference is logged with both values, and the full list goes to `comparison.csv`.

| Article | File | Built from |
|---|---|---|
| Table 4, Figures 2 and 3 | `table4`, `fig2`, `fig3` | `model_results.csv`, cooldown excluded |
| Table 5, Figures 4 and 5 | `table5`, `fig4`, `fig5` | `fusion_results.csv`, the six jointly trained models as in the article |
| Table 6 | `table6` | `fusion_results.csv` and `model_results_with_cooldown.csv` |
| Table 7, Figure 6 | `table7`, `fig6` | `per_fault_type_results.csv`, or rebuilt from `raw_scores/` and the per-fold thresholds for any model the file lacks |
| Table 8, Figure 7 | `table8`, `fig7` | `train_time_s` in `model_results.csv`. Your times reflect your CPU |
| Table 10 | `table10` | the artifact's `prevalence_sensitivity.py` over `raw_scores/`, 5 percent target, best signal per model |
| Table 11 | `table11` | the artifact's `supervised_baseline.py` over the feature tables, plus the DAGMM rows of `model_results_with_cooldown.csv` |
| Table 12 | `table12` | `model_results.csv`, `model_results_with_cooldown.csv` and the artifact's `score_based_analyses.py` over `raw_scores/` |

Use these targets rather than the archive's `code/figures/*.py`. Those are older six-model
scripts that read `results_v3/merged`, a path the archive does not contain. Table 9 is CPU
inference cost, which depends on the machine, so it is not rebuilt.

Figures 8 to 15 (feature importance, separability and training-dynamics diagnostics) come
from `code/pipeline/ae_diagnostics.py` and `code/analysis/supervised_baseline.py`, which are
not wrapped here. Tables 13 to 16 (feature ablation, log representation, window sensitivity,
external validity) are separate studies with their own archived scripts and are out of scope
for `make reproduce`.

## Known differences from the article

`make paper-tables RESULTS=archive` matches 435 of the article's 469 printed cells. The
other 34 differ for the reasons below. None changes a finding.

1. Table 7 is missing the rows "CPU Stress 80%" and "Mem Stress 80%". An unescaped `%` in
   those labels turned the rest of each LaTeX line into a comment, and the two labels were
   joined to the next row. The Average row was computed over all ten faults.
2. Table 7's Deep SVDD and DAGMM columns do not match the archive. Rebuilt from the
   archived per-fold scores and thresholds they average 0.864 and 0.816, against 0.824 and
   0.790 printed. The same rebuild reproduces all 246 archived per-fault rows of the other
   six models exactly.
3. Tables 8 and 11 give the Deep SVDD and DAGMM standard deviations as population standard
   deviations, while every other row uses the sample standard deviation. The three DAGMM
   PR-AUC standard deviations in Table 11 match neither. `make paper-tables` uses the sample
   standard deviation throughout.
4. Tables 10 and 12 paired Deep SVDD and DAGMM with test labels 29 windows off their scores,
   because of a bug in the archived `training.py` (below). AUC moves by at most 0.001 and
   PR-AUC by at most 0.004. On the archive, `make paper-tables` builds both tables twice,
   as archived and with aligned labels, and lists every cell that changes.
5. Two cells of Table 10 are exact ties at the fourth decimal that the article rounded the
   other way.

The label bug is in two places in `training.py`, the saved label files and late fusion. Its
list of sequence models, whose labels are shifted by 29 windows, does not include Deep SVDD
or DAGMM. Metrics computed inside the pipeline (Tables 4 to 8) use aligned labels and are
unaffected, and the article reports fusion only for the other six models. The wrapper fixes
both lines when it loads `training.py`, leaves the archived file unchanged, and records the
fix in every `run_manifest.json`.

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
| `paper/table*.csv` | the article's printed Tables 4 to 8 and 10 to 12, used by `make paper-tables` |

In the archived Table 4, DAGMM scores F1 0.906 and AUC-ROC 0.960 on metrics, 0.818 and
0.897 on traces, and 0.811 and 0.889 on logs. Isolation Forest scores 0.579 and 0.640 on
logs. LSTM-AE (0.207, 0.308, 0.215), CNN1D-AE (0.216, 0.387, 0.400) and LSTM-VAE (0.171,
0.299, 0.266) score AUC-ROC below 0.5 on all three signals.

## What `make verify` compares

`make verify` exits 0 on pass and 1 on any mismatch, and writes its report to
`out/<mode>/verify_report.txt`.

Smoke mode reads `out/smoke` and checks two things.

- Isolation Forest and One-Class SVM, logs, fold 1, at the published Optuna budget (10
  trials). Precision, recall, F1, AUC-ROC and PR-AUC must each land within 0.02 of the
  archived per-fold row. In practice they match to four decimals.
- DAGMM, logs, fold 1, at a reduced budget (2 Optuna trials instead of 25, epochs capped at
  30). It is checked for finite metrics and passed through the FDES checks, but not compared
  numerically, because a reduced search will not land on the published number. The recorded
  venv run gave F1 0.793 and AUC-ROC 0.908, and the container run on the same machine gave F1
  0.806 and AUC-ROC 0.908 (`runs/2026-08-29-smoke-m4pro/docker/`). The archived 25-trial
  values are 0.877 and 0.939.

In the recorded smoke run (`runs/2026-08-29-smoke-m4pro/`) both classical rows match to four
decimals in both environments. Its `fdes_checks.csv` also shows the section 8b guard firing.
One-Class SVM on logs scores F1 0.565 against a predict-all floor of 0.566 with recall 0.975
and is marked EXCLUDE. Isolation Forest (F1 0.580, recall 0.742) and DAGMM (F1 0.793, AUC
0.908) PASS.

Full mode reads `out/full` and checks three things.

- Per (model, signal) mean F1 and mean AUC-ROC against Table 4. Classical models must be
  within 0.02, deep models within max(0.05, 2 x published std). The article trained the deep
  models on GPUs and this package trains them on CPU, so it does not claim bit-for-bit
  agreement with the article.
- The below-chance set must equal `{CNN1D_AE, LSTM_AE, LSTM_VAE}` (3 of 8, 37.5 percent).
- DAGMM must rank first in the Friedman test on each signal with p < 0.05.

`make verify-archive` implements the FDES section 9 requirement that archived tables
regenerate exactly from the archived raw scores. It recomputes `metric_reconciliation.csv`,
Table 4, the below-chance set and the Friedman ranks from the archived scores and per-fold
CSVs and requires equality to three decimals. It runs in about two seconds.

## Runtimes and hardware

Measured on an Apple M4 Pro (12 cores, 24 GB), CPU only, Python 3.11.14, torch 2.5.1, venv
route.

| Target | Wall clock | Notes |
|---|---|---|
| `make fetch` | 34 s | 63 MB download plus md5 and unzip |
| `make smoke` | 150.4 s (2.5 min) | logs signal, fold 1. The two classical models take 10.6 s, the reduced-budget DAGMM the rest |
| `make docker-smoke` | 238.7 s (4.0 min) | same machine, arm64 image, verify PASS, record in `runs/2026-08-29-smoke-m4pro/docker/` |
| `make verify` | under 2 s | |
| `make verify-archive` | 2 s | |
| `make pilot` | 2 s | example Isolation Forest plugin |
| `make check` | under 2 s | sample CSVs in `examples/`, one week at five-minute buckets |
| `make test` | 17 s | 264 unit tests |
| `make paper-tables RESULTS=archive` | seconds | no training |
| `make reproduce` | not run in this repository | see the estimate below |

The artifact records 41.8 hours of training time (Optuna search plus final fit, summed over
all 120 model-signal-fold cells) on the article's SageMaker instances, with the deep models
on NVIDIA A10G GPUs. The metrics signal (532k rows, 64 features) accounts for 27.1 of those
hours.

On a laptop CPU the classical models finish in minutes per signal, and the deep models run
several times slower than on the GPU. `make reproduce` prints an estimate of 2 to 5 days on
an 8-core CPU for the whole grid. The estimate is extrapolated from the smoke timing and the
archived GPU times. It is not a measurement.

## Seed policy and determinism

The wrapper seeds Python `random`, NumPy and PyTorch with 42 + fold id (fold 1 uses 43,
fold 5 uses 47) before each signal and again before each model's Optuna search. A (model,
signal, fold) cell therefore gives the same scores alone as in a full run, so merged slices
match one `make reproduce`. Inside the artifact, Optuna's TPE sampler is seeded with 42,
Isolation Forest uses `random_state=42`, the One-Class SVM subsample uses `RandomState(42)`,
and the baseline (normal-only) split uses `RandomState(42 + fold_id)`, all unchanged.

PyTorch, MKL, oneDNN, OpenBLAS and NumPy pick kernels from the processor they find and split
reductions across the threads they get. That changes results in the last digit, and the
Optuna search can turn those differences into different hyperparameters. Every training run
fixes 8 threads, turns oneDNN off, and on x86 pins the instruction set to AVX2
(`ATEN_CPU_CAPABILITY`, `ONEDNN_MAX_CPU_ISA`, `MKL_CBWR`, `OPENBLAS_CORETYPE` and
`NPY_DISABLE_CPU_FEATURES`). `REPRO_THREADS` changes the thread count, and the numbers change
with it. The settings are written to `run_manifest.json`.

Measured on EC2 in the container, with a reduced-budget run of all eight models on logs,
fold 1:

| Comparison | Result |
|---|---|
| Same machine, run twice | bit-identical, all eight models, Intel and AMD |
| One model alone against the same model in a full run | bit-identical |
| Intel Sapphire Rapids against Intel Ice Lake | bit-identical end to end for the five models tested, and for every operation the models use |
| Intel against AMD | Isolation Forest and One-Class SVM are bit-identical. The deep models differ, because `sqrt` (used by Adam) and the Cholesky factorisation in DAGMM give different results on the two vendors under every MKL setting tried. On the reduced run, DAGMM scores correlate at 0.95 to 0.98 between the two and the other deep models above 0.999. A full run on AMD has not been compared |
| CPU against the article's GPU runs | not bit-identical. See [What `make verify` compares](#what-make-verify-compares) |

Folds are partitioned by fault-injection repetition, never by random window sampling. The test
repetitions are {1,2}, {3,4}, {5,6}, {7,8} and {9,10}. The validation repetition is 10, or 8
for fold 5. Every run writes its seed policy, pinning, package versions and hardware to
`out/<mode>/run_manifest.json`.

## Relationship to FDES and to the IETF draft

Two documents by the same author describe the procedure this code implements. They agree on
the metrics but are different kinds of document.

FDES v1.0.0-draft ([spec repository](https://github.com/mateenali66/failure-detection-evaluation-spec))
is self-published. It turns the evaluation procedure of the IEEE Access study into a
specification with its own section numbers and versioning policy. Nobody has reviewed or
approved it. The check names and section numbers in this repository refer to it.

draft-anjum-nmop-anomaly-detection-evaluation is an Internet-Draft submitted to the IETF
Network Management Operations (NMOP) working group. It adapts the same method to the working
group's network anomaly detection documents and terminology. It is an individual
Informational submission. The working group has not adopted it, it is not an IETF standard
or an RFC, and nothing in it has IETF consensus. Its Section 7 lists this repository under
RFC 7942, which reports implementation status and confers no endorsement.

Read the draft if you came here from the IETF. The table below maps each check to both
documents and says where one covers something the other does not.

The draft covers rule-based and statistical detectors as well as machine-learning ones. The
eight methods reproduced here are all machine-learning methods. The `make check` path
accepts binary alert CSVs from any detector, which is the part of the draft's scope this code
exercises.

## FDES v1.0.0-draft sections implemented

The "Draft section" column maps each check to
[draft-anjum-nmop-anomaly-detection-evaluation](https://datatracker.ietf.org/doc/draft-anjum-nmop-anomaly-detection-evaluation/)
as of revision -02.

| Check | Spec section | Draft section | Where |
|---|---|---|---|
| Predict-all baseline F1 = 2p/(1+p) reported next to every operating-point score | 2, 7 | 3.2 | `fdes/checks.py::predict_all_f1`, `tables/fdes_checks.csv` |
| Threshold-independent metrics from both families (AUC-ROC, PR-AUC) with random references (0.5, p) | 6, 7 | 3.1 | `fdes/checks.py::check_row` |
| Range-based metrics (VUS-PR, VUS-ROC) from the raw score vectors | 6 | none, the draft defines no range-based metrics | `fdes/vus.py`, `tables/vus.csv`, pilot report |
| Exclusion when AUC-ROC is at or below the random reference | 8a | 3.2 | `sec8a_auc_at_or_below_random`, model-level count in `below_chance.json` |
| Flag-everything guard, F1 within 5 percent of the floor, above or below it, with recall at or above 0.95 | 8b | 3.2 | `fdes/checks.py::flag_everything`, `sec8b_flag_everything` |
| Flag-everything guard on the alerted rate. Fires when the alerted rate squared reaches twice prevalence, so a detector may alert often, or far more often than incidents occur, but not both | 8b | none, the draft has no alerted-rate rule | `fdes/checks.py::alert_rate_saturated`, `alert_rate_far_above_prevalence`, `make check` only |
| Alert volume, as alerts per incident and per day | this tool's own check | none, the draft has no alert-volume metric | `fdes/byod.py::alerts_per_incident`, `make check` only |
| Degenerate-detector rule from the artifact's Table 12 support code (AUC <= 0.55 and F1 >= 0.95 x floor, `code/analysis/score_based_analyses.py`) | 8 | 3.2 | `degenerate_table12_rule`, `metric_reconciliation_from_raw_scores` |
| Folds by repetition, threshold on a disjoint validation repetition, raw scores retained | 5 (steps 1 to 3) | 4.3 and 4.4, threshold statement in 3.2 | artifact `training.py`, and `fdes/protocol.py` for pilots |
| Prevalence re-scoring at 1, 5, 10, 20 percent | 5 (step 5) | 3.2 | artifact `code/analysis/prevalence_sensitivity.py` on `results/raw_scores/` |
| Episode-level detection rate. Step 6's detection-latency distribution is not implemented, here or in the artifact | 5 (step 6, in part) | 3.3 | artifact `training.py::evaluate_episode_detection` (`results/episode_results.csv`) |
| Published seed policy, per-fold results, regeneration from archived scores | 9 | 5 and 6 | this README, `expected/model_results_per_fold.csv`, `make verify-archive` |
| Sections 2, 6, 7 and 8 applied to an operator's own alert or score CSVs, with a list of what the input could not support | 2, 6, 7, 8 | 3.1 and 3.2 | `fdes/byod.py`, `make check` |

The 5 percent margin in the section 8b check is this package's value for "the evaluator's
stated margin". It is two sided, so F1 has to sit near the floor from either direction. A
detector well above the floor is not in the flag-everything regime however high its recall
is. Change `FLOOR_MARGIN` in `fdes/checks.py` to use your own margin.

`make check` also applies section 8b to the alerted rate, because the F1 form compares two
numbers derived from the same prevalence and a detector can sit just outside the margin. The
rule and its thresholds are in [docs/METHOD.md](docs/METHOD.md). `make pilot` does not apply
it, because the archived per-fold table has no column for it and `tables/fdes_checks.csv` is
compared byte for byte against the recorded run. Applied to the 120 archived folds it
changes no verdict, so the two paths agree on the published data.

`fdes/vus.py` is a port of the VUS reference implementation from TSB-AD
(https://github.com/TheDatumOrg/TSB-AD, Apache-2.0), the implementation of Paparrizos et
al., PVLDB 15(11), 2022, https://doi.org/10.14778/3551793.3551830, and matches it to float
precision on the archived score vectors. The buffer defaults to the median labeled
anomaly-segment length capped at 100 windows, the reference implementation's `get_metrics`
default, and the value used is reported in every output row. `tables/vus.csv` is computed
from `results/raw_scores/`, which includes cooldown windows, so its values are
cooldown-included. The pilot report's VUS row uses the same cooldown-excluded vector as the
other pilot metrics.

## Run against your own detector

The pilot path evaluates any detector under the FDES procedure on the archived telemetry,
or on your own feature table, and returns a verdict.

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

   Exit code 0 means PASS, 2 means EXCLUDE under section 8, 3 means INSUFFICIENT and 5 means
   PASS with a caveat (see [Exit codes](#exit-codes)). Only a failed command exits 1, so an
   exit of 2 is a rejected detector and not a crash. The report
   (`out/pilot/<name>_<signal>_fold<k>/pilot_report.md`) prints every check with its reference
   value. `pilot_result.json` has the numbers, the fold assignment and the per-check
   `check_status`. The raw test scores and labels are saved as `.npy` for the prevalence
   re-scoring step.

   A fold whose evaluated windows carry only one class is `INSUFFICIENT`. AUC-ROC is undefined
   there and precision, recall and F1 carry no information, so every check reports `not
   evaluable` and the report says why. The archived folds all carry both classes, but a
   feature table passed with `--features` might not.

3. To use your own telemetry, pass `--features path/to/features.parquet`. The table must
   contain `label` (0 or 1 per window), `rep` (repetition id 1 to 10, used for the fold split),
   `phase` (`baseline` for normal-only collection, anything else for the fault campaign),
   `timestamp` and `fault_type` (used for cooldown marking and per-fault reporting), plus
   numeric feature columns. Columns named `service, run_id, testbed, count, fold` are treated
   as metadata and ignored as features.

The shipped example, `detectors/example_isolation_forest.py`, uses the hyperparameters Optuna
selected for the article's Isolation Forest on logs fold 1. `make pilot` runs it and reproduces
that archived row exactly (precision 0.4755, recall 0.7420, F1 0.5796, AUC-ROC 0.6355,
PR-AUC 0.5321 on 10,449 cooldown-excluded windows), which confirms the pilot protocol scores
a detector the way the article did.

## Check your own data

The pilot path needs the archived feature tables and a detector that returns one score per
window. Detectors built into monitoring products (Datadog Watchdog and `anomalies()` monitors,
Splunk ITSI adaptive thresholding and MLTK, Dynatrace Davis) produce alert windows instead,
and the ground truth is usually a list of incident windows from a postmortem or an incident
tracker.

`make check` takes that input. It buckets a timeline, marks every bucket as alerted or not
and as inside an incident or not, and runs the FDES section 2, 7 and 8 checks the article
used. It needs no Zenodo artifact, no detector plugin and no GPU.

If a verdict surprises you, [docs/METHOD.md](docs/METHOD.md) explains what each check measures
and what it cannot tell you.

### Thirty seconds, on the sample data

```bash
make check                                    # the good sample detector, verdict PASS
make check ALERTS=examples/alerts_useless.csv # the useless one, verdict EXCLUDE
```

`examples/` has seven synthetic CSVs (one ground truth and six detectors) and the script that
generates them (`examples/make_examples.py`). The scenario is one week with five incidents
covering about 5 percent of it. Each file uses a different timestamp format, so running them
all shows what the tool assumed about each.

| File | What it is | Verdict | Exit code |
|---|---|---|---|
| `incidents.csv` | the ground truth, five incident windows | | |
| `alerts_good.csv` | catches four incidents of five and fires three times when nothing was wrong | PASS | 0 |
| `alerts_useless.csv` | a static threshold on a metric that swings with the nightly batch window, so it tracks the clock rather than incidents | EXCLUDE | 2 |
| `alerts_everything.csv` | one window over the whole range | EXCLUDE | 2 |
| `scores_good.csv` | an anomaly score that rises during incidents | PASS | 0 |
| `scores_useless.csv` | a plausible-looking score with no relationship to the incidents | EXCLUDE | 2 |
| `scores_flat.csv` | a model that collapsed and emits one constant number | EXCLUDE | 2 |

`alerts_useless.csv` scores F1 0.051, against a predict-all floor of 0.096 at that
prevalence, so flagging every bucket would score twice as well. `scores_useless.csv` clears
the floor slightly with F1 0.097, and its AUC-ROC of 0.358 shows it ranks the buckets worse
than chance.

### On your own data

Two modes. Both take a ground-truth CSV of incident windows.

Ground truth has to come from somewhere the detector cannot reach. If your incident windows
were built from the alerts you are scoring, the result is circular and will usually look
excellent. Read [Where the incident windows came from](docs/METHOD.md#where-the-incident-windows-came-from)
before you build the incidents file.

Alerts mode takes a CSV of alert windows, a CSV of incident windows, a time range and a
bucket size. Use it for a vendor product.

```bash
python bin/reproduce.py check \
  --alerts my_alerts.csv \
  --incidents my_incidents.csv \
  --bucket 5m \
  --from 2026-03-01T00:00:00Z \
  --to 2026-03-08T00:00:00Z
```

Scores mode takes a CSV with a timestamp column and a score column, plus the same incident
windows. It is the only mode that supports the rank metrics.

```bash
python bin/reproduce.py check \
  --scores my_scores.csv \
  --incidents my_incidents.csv \
  --bucket 5m \
  --from 2026-03-01T00:00:00Z \
  --to 2026-03-08T00:00:00Z \
  --threshold 0.82
```

The same through `make`:

```bash
make check ALERTS=my_alerts.csv INCIDENTS=my_incidents.csv BUCKET=5m \
  FROM=2026-03-01T00:00:00Z TO=2026-03-08T00:00:00Z
make check SCORES=my_scores.csv INCIDENTS=my_incidents.csv BUCKET=5m \
  FROM=2026-03-01T00:00:00Z TO=2026-03-08T00:00:00Z THRESHOLD=0.82
```

The run writes `out/check/<name>/check_result.json` and `check_report.md`.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | `PASS`, with nothing held back |
| 1 | A usage error. The command was wrong. This is not a verdict |
| 2 | `EXCLUDE` |
| 3 | `INSUFFICIENT`, the input cannot support a verdict |
| 4 | `UNSTABLE`, the verdict is not stable across bucket sizes |
| 5 | `PASS`, but something was held back |

A run exits 5 when the verdict is `PASS` and either an exclusion was suppressed because the
alerted rate was a bucketing artefact, or the detector alerted far more often than there were
incidents to find. `pass_qualified` in `check_result.json` says the same, and the report
names the caveat and the reason.

Whether 5 should fail a build is your decision. A detector that alerts briefly and
occasionally can legitimately exit 5, so treating 5 as a failure will reject working
detectors. Treating it as 0 will pass a detector that pages more often than anyone would
read.

### The four verdicts

`EXCLUDE` means fix the detector. `INSUFFICIENT` means fix the input. `UNSTABLE` means the
input and the bucket size together cannot settle it. The pilot path uses the first three
verdicts and the same exit codes.

| Verdict | Exit code | What it means | What to do |
|---|---|---|---|
| `PASS` | 0 | the detector cleared every check that could be evaluated | read the report for which checks those were |
| | 1 | not a verdict. The command failed on a bad argument, an unreadable CSV or a missing file, and printed the reason to stderr | fix what the message names |
| `EXCLUDE` | 2 | the input supported a verdict and the detector failed a check | the detector is not worth deploying as it stands |
| `INSUFFICIENT` | 3 | the input could not support a verdict either way | fix the CSVs or the range, then run it again |
| `UNSTABLE` | 4 | the verdict changed across bucket sizes, so no single verdict is the result (alerts mode) | pick the bucket from the time scale your responders work at and say why, or treat it as undecided |

`--no-sweep` holds the verdict and the exit code at the bucket you passed. The sweep still
runs, and a run that would have been `UNSTABLE` says so at the top of the report and names
the exit code it would have had.

Only exit 1 means the command failed, so continuous integration can branch on the code
without reading the output. `make verify` uses 0 for a match and 1 for a mismatch. The same
table is in `python bin/reproduce.py check --help` and `pilot --help`.

A run is `INSUFFICIENT` when any of these hold.

- Prevalence is zero. No bucket in the range falls inside an incident window.
- Every incident window falls entirely outside the range.
- The incident CSV has no rows.
- Every bucket falls inside an incident window, so the ground truth has one class.
- Every alert window falls entirely outside the range, which points at a wrong `--from` and
  `--to`. An alert CSV with no rows at all is different. That is a detector that never fired,
  and it gets a real verdict.

On an `INSUFFICIENT` run no check is applied and every check reports `not evaluable`. At
prevalence zero the predict-all floor is also zero, so an F1 of zero sits exactly on the
floor and three checks would otherwise read `pass` on a run that holds no information. The
reason the run could not be evaluated is printed at the top, next to the verdict.

### The CSV format

Alert and incident CSVs need a start column and an end column. Score CSVs need a timestamp
column and a score column. Column names are matched case insensitively against names that
real exports use. `start`, `start_time`, `started_at`, `triggered_at` and `from` are read as
a start, `end`, `end_time`, `resolved_at` and `to` as an end, `timestamp`, `time`, `ts` and
`_time` as a time, and `score`, `anomaly_score`, `value` and `deviation` as a score. Name any
other column with `--start-col`, `--end-col`, `--timestamp-col` or `--score-col`. Extra
columns are ignored, except that a `severity` or `priority` column is counted in the report
and a `service` or `scope` column is used for the scope report described in
[docs/METHOD.md](docs/METHOD.md).

The alerts CSV and the incidents CSV often disagree on header names. A Watchdog export uses
`triggered_at` and `resolved_at` while an incident tracker uses `start` and `end`, so each
file takes its own override. `--start-col` and `--end-col` name the columns of the alerts
CSV. `--incident-start-col` and `--incident-end-col` name the columns of the incidents CSV
and fall back to `--start-col` and `--end-col` when not given. In scores mode the incidents
CSV is the only window file, so both pairs apply to it under the same fallback.

```bash
python bin/reproduce.py check \
  --alerts watchdog_export.csv --start-col triggered_at --end-col resolved_at \
  --incidents incident_tracker.csv --incident-start-col start --incident-end-col end \
  --bucket 5m --from 2026-03-01T00:00:00Z --to 2026-03-08T00:00:00Z
```

An alerts CSV with a start but no end is read as point events, one bucket each. The report
says so next to the verdict and names the columns the file did have. A Splunk export with
`_time`, `_indextime`, `earliest` and `latest` has a recognised start in `_time` and no
recognised end, so every row becomes an instant. Pass `--end-col latest` (or
`--incident-end-col` for the incidents file) to read the windows.

The schema is read from the header, so a file with a header and no rows is still described
by the columns it declares. An alerts CSV with a header and no rows is read as a detector
that never fired, and it gets a real verdict.

A row that ends before it starts, or ends the same second it starts, is refused. Both cover
no time and both are export problems. The message names the two columns and how many rows
are affected. Point events are not affected, because a file with no end column has no end to
compare.

Timestamps can be ISO 8601 or epoch seconds. Different files may use different formats, but
one column must use one format.

```
start,end
2026-03-01T14:20:00Z,2026-03-01T15:15:00Z
2026-03-02T15:05:00+00:00,2026-03-02T17:15:00+00:00
```

A timestamp with no timezone is read as UTC and the report says so. That is harmless when the
whole export is in one local zone, because the same offset applies to every file. Mixing
timezone-aware and naive rows inside one file is reported. Epoch milliseconds are refused with
a message telling you to divide by 1000. Sub-second precision is dropped.

## Layout

```
bin/reproduce.py        single entry point (fetch, smoke, reproduce, merge, verify, verify-archive, estimate, pilot, check, paper-tables, figures)
bin/build_expected.py   regenerates expected/ from the fetched artifact
fdes/checks.py          FDES section 2, 6, 7, 8 checks
fdes/protocol.py        FDES section 5 procedure for pilots (reuses the artifact's split, cooldown, threshold and metric code)
fdes/tables.py          Table 4, 5, 7, 8, below-chance, Friedman, VUS
fdes/paper.py           the article's Tables 4 to 12 and Figures 2 to 7, behind `make paper-tables` and `make figures`
fdes/vus.py             VUS-PR and VUS-ROC, ported from the TSB-AD reference implementation
fdes/byod.py            the bring-your-own-data path behind `make check`
detectors/base.py       plugin interface; detectors/example_isolation_forest.py
examples/               sample alert, incident and score CSVs plus the script that makes them
tests/                  unit tests (`make test`, no Zenodo needed)
docs/METHOD.md          what each check does and why
expected/               archived values verify compares against, and the article's printed tables
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
  version   = {1.4.1},
  publisher = {Zenodo},
  year      = {2026},
  doi       = {10.5281/zenodo.23275206}
}
```

This reproduction package is archived at Zenodo, DOI
[10.5281/zenodo.23275206](https://doi.org/10.5281/zenodo.23275206) (v1.4.1, concept DOI
[10.5281/zenodo.22170201](https://doi.org/10.5281/zenodo.22170201) resolves to the latest version).

The specification is the Failure Detection Evaluation Specification v1.0.0-draft,
https://github.com/mateenali66/failure-detection-evaluation-spec. `CITATION.cff` holds the
machine-readable metadata.

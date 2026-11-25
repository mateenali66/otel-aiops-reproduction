# Reproduction package: ML anomaly detection on unified OpenTelemetry telemetry

| | |
|---|---|
| Article | M. A. Anjum, "Evaluating ML-Based Anomaly Detection on Unified OpenTelemetry Telemetry: An Empirical Study Across Traces, Metrics, and Logs," IEEE Access, vol. 14, pp. 93576-93608, 2026. DOI [10.1109/ACCESS.2026.3705430](https://doi.org/10.1109/ACCESS.2026.3705430). Open access, CC BY 4.0 |
| Author | Mateen Ali Anjum, Phono Technologies Inc., Toronto, ON, Canada. mateen@phonotech.ca |
| Package | otel-aiops-reproduction 1.5.0, this README dated 2026-11-24. [github.com/mateenali66/otel-aiops-reproduction](https://github.com/mateenali66/otel-aiops-reproduction), tag `v1.5.0`, archived on Zenodo at [10.5281/zenodo.24518271](https://doi.org/10.5281/zenodo.24518271) (concept DOI [10.5281/zenodo.22170201](https://doi.org/10.5281/zenodo.22170201) resolves to the latest version) |
| Data | Zenodo [10.5281/zenodo.22078287](https://doi.org/10.5281/zenodo.22078287) (record v3.1.3), raw telemetry on Figshare [10.6084/m9.figshare.34669797](https://doi.org/10.6084/m9.figshare.34669797). See [Data availability](#data-availability) |

This repository reproduces the article's tables and figures from its published Zenodo
artifact. It runs in a pinned container or a pinned virtual environment, and every result it
produces is checked against the article.

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
| Reproduce the article's tables and figures | [Quick start](#quick-start), then [Every table and figure](#every-table-and-figure-and-where-it-comes-from) |
| Retrain the models and check the result | [Retraining](#retraining), then [What `make verify` compares](#what-make-verify-compares) |
| Check your own monitoring data | [Check your own data](#check-your-own-data). Two CSVs, no download, about two minutes |
| Put your own detector through the procedure | [Run against your own detector](#run-against-your-own-detector) |

## Quick start

```bash
git clone https://github.com/mateenali66/otel-aiops-reproduction && cd otel-aiops-reproduction
python3.11 -m venv venv && venv/bin/pip install -r requirements.txt -c constraints.txt   # or: make docker-build
make fetch     # downloads both files of the Zenodo record (63 MB), checks their md5, unpacks to data/
make paper     # every table and figure of the article, no training, 5 to 7 minutes
make test      # unit tests
```

The venv route needs Python 3.11, the version the container uses and every recorded run was
made with. If your system has no `python3.11` (a stock Mac has 3.9), install it before creating
the venv, for example from python.org or with `uv python install 3.11`, which puts `python3.11`
in `~/.local/bin`. Recorded runs used 3.11.14 and 3.11.18. The container pins its Python by
image digest. Every `make` target then runs on
`venv/bin/python`, or `venv/Scripts/python.exe` under Git Bash on Windows, without activating
it. `make help` prints the route it will take. `requirements.txt` pins the nine packages the code
imports and `constraints.txt` pins everything they pull in.

`make paper` passes when every printed cell of Tables 4 to 16 has been reproduced or is listed,
with its reason, in `expected/paper/exceptions.csv`. Its report, `out/paper/paper_report.txt`,
gives the count for each table, every cell that differs and the comparisons behind the figures.
A run on a clean machine ends like this:

```
printed cells in Tables 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16: 575
  reproduced exactly: 550
  listed in expected/paper/exceptions.csv with a reason: 25
cells in the LaTeX source but not in the typeset article (expected/paper/source_only.csv): 16, 12 reproduced exactly, 4 listed
    Table 7: 20 cells (No archived file produces this cell. ...)
    Table 11: 3 cells (The printed mean matches. ...)
    Table 14: 6 cells (Set by the Drain/TF-IDF/PCA fit on the raw log corpus, ...)
RESULT: PASS
```

PASS means every printed cell is either reproduced or listed with its reason. It does not mean
all 575 are reproduced: 25 are not, and the list says why for each. `out/paper/paper_manifest.json`
records the package version, the Python and library versions, the machine, and the md5 of the
Zenodo files the run read.

`bin/reproduce.py` exits 0 on PASS and 1 on FAIL. Through `make`, a failed command prints
`make: *** [paper] Error 1` and make itself exits 2, whatever the command's code was, so for
scripting call `bin/reproduce.py` directly (`venv/bin/python bin/reproduce.py paper`).

The container route needs no local Python. After `make docker-build`, every target runs
inside the image with `data/` and `out/` mounted from the checkout. `NO_DOCKER=1` runs a
target on the local Python instead.

```bash
make docker-build     # python:3.11-slim pinned by digest, CPU torch, exact pins
make docker-smoke     # fetch, smoke and verify inside the container
```

Other targets:

```bash
make smoke            # one signal, one fold of training: a few minutes on a laptop CPU
make verify           # compares out/smoke against expected/, exit code 1 on mismatch
make verify-archive   # regenerates the archived tables from the archived raw scores (seconds)
make pilot            # runs the example detector plugin under the FDES procedure
make check            # runs the FDES checks on the sample CSVs in examples/
```

`make check` and `make test` need neither the Zenodo download nor a GPU. Tests that need the
download skip themselves without it.

`bin/reproduce.py` is the single entry point behind every target, and `python
bin/reproduce.py -h` lists the subcommands. No cloud account, S3 bucket or SageMaker role is
needed. The artifact's SageMaker orchestration (`run_pipeline.py`) is not used. A retrain still
prints "SageMaker Training" at the start, because that banner is part of the archived
`training.py`. The `PAPER5_` environment variables in its log and manifest are the archived
`training.py`'s own settings. "Paper 5" is the author's working name for the article, and it
also names the record's second file.

## Two ways to reproduce the article

**From the deposit, without training (`make paper`).** The Zenodo record keeps every model's
anomaly scores for every signal and fold. `make paper` recomputes every per-fold result from
those scores with the artifact's own `training.py` functions (its test split, cooldown rule,
metrics, late fusion and per-fault evaluation), checks each recomputed value against the
archived results, and builds every table and figure from there. It reproduces the article
exactly, apart from the 29 listed cells, on any machine, because nothing in it depends on
training.

**By retraining (`make reproduce`, then `make verify MODE=full`).** This runs the archived
training pipeline again. Isolation Forest and One-Class SVM come out within 0.02 of the
article, and in practice to four decimals. The deep models do not reproduce the article's
digits, on any hardware, for the reason in the next section, so their results are checked
against a declared spread instead.

## Why a retrain cannot give the article's deep-model digits

The archived training code (`code/pipeline/training.py` and `run_pipeline.py`) never seeds
PyTorch or NumPy, and its data loader shuffles the training windows. Each deep-model number
in the article is therefore one draw from a run whose random state was not recorded, and no
one can regenerate that draw, on the article's GPUs or anywhere else.

This package seeds every run (Python, NumPy and PyTorch with 42 + fold id) and pins threads
and instruction sets, so its own retrains repeat bit for bit on the same kind of processor.
They are still different draws from the article's.

To measure how far a retrain moves, the six deep models were retrained on fold 5 with the
article's own hyperparameters under ten seeds (1001 to 1010), on Intel CPUs with the pinning
above. The outputs, the script and the per-run manifests are in `supplement/seed_test/`.

- In the 42 cells (model, signal, metric) whose ten-seed range is wider than 0.002 (the
  `varies` column of `supplement/seed_test/seed_spread.csv`), the article's
  fold 5 value falls inside the ten-seed range in 35. If the article's value were one more
  draw from the same spread it would fall inside in 34.4 on average, so the article's numbers
  look like ordinary draws.
- The spread depends on the model. LSTM-VAE metrics AUC-ROC does not move (0.170 for every
  seed). Transformer-AE metrics AUC-ROC runs from 0.401 to 0.983, and Deep SVDD traces
  AUC-ROC from 0.189 to 0.926.
- The findings hold in every seed. CNN1D-AE, LSTM-AE and LSTM-VAE score AUC-ROC below 0.5 on
  every signal in all ten, and no other model does. DAGMM has the best logs AUC-ROC in all
  ten. With each seed run in place of the archived fold 5, DAGMM still ranks first in the
  Friedman test on every signal (p at or below 0.002).

`expected/deep_seed_spread.csv` holds the range per model, signal and metric.
[What `make verify` compares](#what-make-verify-compares) gives the gap a retrain may show in
each deep-model cell.

## Data availability

Every file the package reads is public. Nothing needs an application or an account.

| Data | Source and creation | File | Format | Where | How the package gets it | License |
|---|---|---|---|---|---|---|
| Feature tables, per-fold results, raw anomaly scores, pipeline and analysis code | Created by the author. OpenTelemetry traces, metrics and logs from two microservice testbeds on AWS EKS, under a Chaos Mesh fault-injection campaign of 33.5 hours (March 6 to 8, 2026, UTC), processed into 60-second feature windows, and the results of the article's runs | `otel-aiops-benchmark.zip`, 62.9 MB | Parquet, CSV, NumPy `.npy`, Python | Zenodo [10.5281/zenodo.22078287](https://doi.org/10.5281/zenodo.22078287), record v3.1.3 | `make fetch`, md5 checked, into `data/zenodo/otel-aiops-benchmark/` | CC BY 4.0 |
| External-validity results (Table 16) | Created by the author: the article's models run on the RCAEval benchmark and the Alibaba 2021 production trace. Those third-party datasets are not redistributed. The file's README cites their sources | `paper5_external_validity_code_v3.1.1.zip`, 21 KB | CSV, Python | the same Zenodo record | `make fetch`, md5 checked, into `data/zenodo/external-validity/` | CC BY 4.0 |
| Figure inputs (Figures 8 to 15) | Created by the author in April 2026: the separability analysis outputs and the per-epoch diagnostics of instrumented autoencoder runs | `supplement/separability_original/`, `supplement/ae_diagnostics/` | CSV, NPZ, JSON, Parquet | this repository | included | CC BY 4.0 |
| Seed test | Created by the author in November 2026 with `supplement/seed_test/seedtest.py` | `supplement/seed_test/` | CSV, JSON | this repository | included | CC BY 4.0 |
| The article's printed values | Taken from the article's LaTeX source and checked against the typeset article | `expected/paper/` | CSV | this repository | included | CC BY 4.0 |
| Raw traces, metrics and logs | The campaign's raw telemetry, scrubbed of identifiers | 10 files, 2.96 GB | as exported | Figshare [10.6084/m9.figshare.34669797](https://doi.org/10.6084/m9.figshare.34669797) | not read by any target | CC BY 4.0 |

## Computing requirements

- Software: Python 3.11 with the 36 packages pinned in `requirements.txt` and
  `constraints.txt` (numpy 1.26.4, pandas 2.2.3, scikit-learn 1.6.1, scipy 1.13.1, optuna
  4.1.0, torch 2.5.1 CPU, pyarrow 17.0.0, matplotlib 3.9.2, seaborn 0.13.2), or Docker with the
  `Dockerfile` (python:3.11-slim pinned by digest). GNU make. On Windows, Git Bash.
- Hardware: any 64-bit CPU, no GPU. Tested on Apple M5 and M3 Pro laptops (macOS 26 and 27)
  and in Linux containers on arm64 and on x86_64. A container with 12 GB of memory was
  enough for every target.
- Disk: 185 MB for the Zenodo download unpacked, about 90 MB for `make paper`'s output, and
  1 GB for the venv or 1.75 GB for the Docker image.
- Runtime: about 6 minutes for `make paper`, see [Runtimes and hardware](#runtimes-and-hardware).

## Every table and figure, and where it comes from

`make paper` (`bin/reproduce.py paper`) is the one command behind all of them. They are written to `out/paper/tables/` (CSV and LaTeX) and
`out/paper/figures/` (PDF and PNG). The "Cells" column counts printed cells reproduced
exactly, out of those printed. Every comparison is in `out/paper/tables/comparison.csv`.

| Article | Output | Code | Built from | Cells |
|---|---|---|---|---|
| Tables 1, 2, 3, 17 | none | none | Descriptive: positioning, configuration, the search spaces in `training.py`, the fault mapping. Nothing to compute | |
| Table 4, Figures 2 and 3 | `table4`, `fig2`, `fig3` | `fdes/rebuild.py`, `fdes/paper.py` (`table4`, `write_figures`) | per-fold metrics rebuilt from the raw scores (cooldown excluded) | 120 of 120 |
| Table 5, Figures 4 and 5 | `table5`, `fig4`, `fig5` | `fdes/rebuild.py` (`fusion_rows`), `fdes/paper.py` (`table5`) | late fusion rebuilt from the raw scores with `run_late_fusion`, the six jointly trained models as in the article | 96 of 96 |
| Table 6 | `table6` | `fdes/paper.py` (`table6`) | rebuilt fusion and cooldown-included metrics | 48 of 48 |
| Table 7, Figure 6 | `table7`, `fig6` | `fdes/rebuild.py`, `fdes/paper.py` (`table7`) | per-fault recall rebuilt from the raw scores and the per-fold thresholds. Figure 6 shows the six models the article plots, and `fig6_per_fault_recall_heatmap_all_models` adds Deep SVDD and DAGMM | 56 of 72 |
| Table 8, Figure 7 | `table8`, `fig7` | `fdes/paper.py` (`table8`) | training times recorded by the original run | 32 of 32 |
| Table 9 | `table9` | `fdes/extra_tables.py` (`table9`) | single-window timings recorded by `inference_benchmark.py`, one row per model: mean of the three median latencies, the largest p95, the lowest throughput, the largest model | 32 of 32 |
| Table 10 | `table10` | `fdes/paper.py` (`table10`), artifact `code/analysis/prevalence_sensitivity.py` | the artifact's `prevalence_sensitivity.py` over the raw scores | 40 of 40 |
| Table 11 | `table11` | `fdes/paper.py` (`table11`, `rf_rerun_report`), artifact `code/analysis/supervised_baseline.py` | the article's Random Forest results deposited on Zenodo, and the rebuilt cooldown-included DAGMM rows. The Random Forests are also trained again and compared, see below | 15 of 18 |
| Table 12 | `table12` | `fdes/paper.py` (`table12`), artifact `code/analysis/score_based_analyses.py` | the artifact's `score_based_analyses.py` over the raw scores | 27 of 27 |
| Table 13 | `table13` | `fdes/extra_tables.py` (`table13`) | the deposited feature-ablation results | 18 of 18 |
| Table 14 | `table14` | `fdes/extra_tables.py` (`table14`), artifact `code/analysis/summarize_log_repr.py` | the deposited log-representation results, summarised by the artifact's `summarize_log_repr.py`, and Table 4's logs rows | 18 of 24 |
| Table 15 | `table15` | `fdes/extra_tables.py` (`table15`), artifact `code/analysis/window_sensitivity_summary.py` | the deposited window-size results, summarised by the artifact's `window_sensitivity_summary.py` | 24 of 24 |
| Table 16 | `table16` | `fdes/extra_tables.py` (`table16`) | the external-validity replication results in the record's second file | 24 of 24 |
| Figure 1 | none | none | architecture diagram, not data | |
| Figures 8 to 11 | `fig8` to `fig11` | `supplement/figure_scripts/generate_separability.py`, run by `fdes/figures_extra.py` | the article's separability outputs (`supplement/separability_original`), drawn by the article's script. The analysis is also run again on the deposit and compared, see below | |
| Figures 12 to 15 | `fig12` to `fig15` | `supplement/figure_scripts/generate_ae_diagnostics.py`, run by `fdes/figures_extra.py` | the recorded per-epoch diagnostics of the five autoencoder training runs the figures plot (`supplement/ae_diagnostics`, which also holds the RobustScaler control run the article describes in its text), drawn by the article's script | |

In total 550 of the 575 printed cells in Tables 4 to 16 are reproduced exactly. The other 25
are listed in [Known differences from the article](#known-differences-from-the-article). Of
the 550, 373 are recomputed from the archived anomaly scores, 64 restate training and inference
times the original runs recorded (Tables 8 and 9), 92 aggregate per-fold results that separate
studies deposited (Table 11's Random Forest rows and Tables 13 to 16), and 21 are text labels,
such as the best signal per model in Table 10. Sixteen more cells are in the article's LaTeX
source but were lost when the article was typeset (see the Table 7 note below). They are
compared too and counted apart.

Tables 13 to 15 report separate studies (feature ablation, log representation, window size)
whose training runs are not repeated here. Table 16 used third-party datasets that are not
redistributed. Their tables are rebuilt from the per-fold results each study deposited.

`supplement/` holds what the Zenodo record lacks: the article's scripts for Figures 8 to 15
(`supplement/figure_scripts`, copied unchanged apart from the edits each script's header
lists), the outputs those figures plot, and the seed test. The scripts name their files after
the LaTeX source (fig9 to fig16). The copies `make paper` writes carry the printed numbers.

The supervised Random Forests behind Table 11 are seeded. On macOS they reproduce the archived
per-fold results exactly. On Linux, which includes the container, they differ by up to 0.003
per fold, enough to move one printed standard deviation in the third decimal. So Table 11 is
built from the deposited results, and `make paper` trains the Random Forests again and writes
the largest per-fold gap to its report.

The separability analysis behind Figures 8 to 11 (`feature_importance_separability.py`) is
seeded, so `make paper` runs it again on the deposit. The class-separability values (Figure 9)
and the anomaly-score statistics (Figure 11) agree with the article's outputs to within 1e-6,
which `make paper` requires. The overlap index in the same file, which no figure plots,
differs by up to 0.011 and is reported. Under the pinned library versions the Random Forest importances
(Figure 8) do not: they differ by up to 0.016 on metrics, 0.000 on logs and 0.076 on traces,
where the top three features come out in a different order. The t-SNE layout (Figure 10) also
differs, over the same 3000 sampled windows. Both figures are therefore drawn from the
article's outputs, and the gaps are written to the report. The
autoencoder runs behind Figures 12 to 15 were not seeded and ran on a GPU, so they cannot be
run again to the same values.

So Figures 2 to 7 are computed from the deposit by this package. Figures 8 to 15 are drawn by
the article's own scripts from the article's recorded outputs in `supplement/`. For Figures 9
and 11 the rerun reproduces those outputs to within 1e-6. The scripts' layout is kept as it
is, so the clipped axis label on Figure 8's traces panel is also in the article.

`make paper-tables` and `make figures` build the same tables and Figures 2 to 7 from any
results directory: `out/full/results` after a retrain, or `RESULTS=archive` for the Zenodo
results as archived. On a retrain they train the Random Forests of Table 11 again.

The comparison values in `expected/paper/` were taken from the article's LaTeX source and
checked against the typeset article (the IEEE proof of the published version): every value
printed there appears in it.

## Known differences from the article

Three of the article's computations differ from the convention of the rest. `make paper`
reproduces each as printed and writes the alternative beside it.

- Table 8 prints the Deep SVDD and DAGMM standard deviations, and Table 11 the DAGMM F1 and
  AUC-ROC standard deviations, as population standard deviations, and every other row as the
  sample standard deviation. Both tables are built that way, and `table8_sample_sd.csv` and
  `table11_sample_sd.csv` use the sample standard deviation throughout.
  `sample_sd_changes.csv` lists the 12 cells that differ between the two builds.
- Table 10 is rounded half to even, so its two values that sit exactly on a half print as
  0.052 (0.0515) and 0.874 (0.8745). Every other table uses Python's rounding.
- Tables 10 and 12 paired Deep SVDD and DAGMM with test labels 29 windows off their scores,
  because of a bug in the archived `training.py` (below). Both tables are built as archived,
  which reproduces the print, and again with aligned labels (`table10_aligned_labels.csv`,
  `table12_aligned_labels.csv`). No printed cell moves by more than 0.006 (Table 12, Deep SVDD
  traces, normalised PR lift), and `aligned_labels_changes.csv` lists every cell that moves.

The 29 cells listed in `expected/paper/exceptions.csv`, each with its reason (25 printed, 4
in the two Table 7 rows lost in typesetting):

1. Table 7, 20 of the 22 Deep SVDD and DAGMM cells (16 printed, 4 in the lost rows). No archived
   file produces them. Rebuilt from the archived per-fold scores and thresholds, the columns
   average 0.864 and 0.816, against 0.824 and 0.790 printed. The Cascade SS Cat+Ord cells
   (0.933) match. The same rebuild reproduces every archived per-fault row of the other six
   models, on all three signals. The rebuilt values are in `out/paper/tables/table7.csv`.
2. Table 11, the three DAGMM PR-AUC standard deviations. The means match. The standard
   deviations match neither formula, with cooldown included or excluded.
3. Table 14, the dimension and explained-variance cells of the three Drain/TF-IDF rows (6
   cells). They are set by the PCA fit on the raw log corpus, which the Zenodo record does not
   contain. The F1 and AUC-ROC cells of those rows are reproduced.

In the typeset article, Table 7 lacks the values of the rows "CPU Stress 80%" and "Mem Stress
80%": an unescaped `%` in those labels turned the rest of each LaTeX line into a comment, so
the two labels run into the next row. The rows are in the article's LaTeX source, and the
Average row was computed over all ten faults. Their 16 cells are listed in
`expected/paper/source_only.csv` and counted apart from the printed cells.

The label bug is in two places in `training.py`, the saved label files and late fusion. Its
list of sequence models, whose labels are shifted by 29 windows, does not include Deep SVDD
or DAGMM. Metrics computed inside the pipeline (Tables 4 to 8) use aligned labels and are
unaffected, and the article reports fusion only for the other six models. On a retrain, the
wrapper fixes both lines when it loads `training.py`, leaves the archived file unchanged, and
records the fix in every `run_manifest.json`.

None of these changes a finding.

## Retraining

The full retrain covers 8 models x 3 signals x 5 folds. Read the runtimes section before
starting it.

```bash
make reproduce                       # prints the estimate, then runs everything
make reproduce FOLDS=1               # or one fold at a time
make verify MODE=full                # works on any set of folds
```

Each fold is written to `out/full/fold<k>`, and every `make reproduce` rebuilds
`out/full/results` from all the fold directories there. Running the folds one at a time
therefore ends with the same five-fold results as one run. `make merge` rebuilds
`out/full/results` from those directories without training.

To split the grid across machines, run slices with `bin/reproduce.py reproduce --out
<dir> --folds ... --models ...`, each covering all three signals, and join them with
`make merge PARTS="<dir> <dir> ..."`. A merged run gives the same numbers as one `make
reproduce` (see [Seed policy and determinism](#seed-policy-and-determinism)).

A retrain runs `training.py` in its local code path with CPU forced. The pipeline runs as
archived, with the label fix above applied at load time.

| Output file (under `out/<mode>/`) | What it is | Produced by |
|---|---|---|
| `results/model_results.csv` | per-fold rows behind Table 4 (cooldown excluded) | artifact `training.py` |
| `results/model_results_with_cooldown.csv` | per-fold rows with cooldown included | artifact `training.py` |
| `tables/table4_single_signal.csv` | Table 4 (mean and std over folds) | `fdes/tables.py` |
| `tables/table5_fusion.csv` | Table 5 late fusion (needs a run with 2+ signals) | `fdes/tables.py` from `results/fusion_results.csv` |
| `tables/table7_per_fault_recall.csv` | Table 7 per-fault recall | `fdes/tables.py` from `results/per_fault_type_results.csv` |
| `tables/table8_training_time.csv` | Table 8 (times reflect your CPU) | `fdes/tables.py` |
| `tables/below_chance.json` | models with mean AUC-ROC below 0.5 on every signal (Section V-B, FDES section 8) | `fdes/checks.py` |
| `tables/fdes_checks.csv` | FDES sections 7 and 8 per (model, signal, fold) | `fdes/checks.py` |
| `tables/friedman_ranking.csv` | Friedman ranks per signal (Section V-C, needs 3+ folds and fusion results) | artifact `significance_tests.py` |
| `results/optuna_results.csv` | selected hyperparameters | artifact `training.py` |
| `results/raw_scores/*.npy` | per-window scores and labels | artifact `training.py` |
| `out/pilot/<detector>_<signal>_fold<k>/pilot_report.md` | FDES verdict for your own detector | `fdes/protocol.py` |
| `out/check/<name>/check_report.md` | FDES verdict for your own alert or score CSVs | `fdes/byod.py` |

`make paper-tables` builds the article's tables from a retrain's `out/full/results`.

## Expected output

| File | Content |
|---|---|
| `expected/model_results_per_fold.csv` | the 120 archived per-fold rows (8 models x 3 signals x 5 folds) |
| `expected/table4_single_signal.csv` | Table 4 as mean and std |
| `expected/below_chance.json` | `["CNN1D_AE", "LSTM_AE", "LSTM_VAE"]`, 3 of 8 = 0.375 |
| `expected/friedman_ranking.csv` | archived 8-model Friedman ranks, DAGMM rank 1 on every signal, p < 0.001 |
| `expected/metric_reconciliation.csv` | archived Table 12 support (AUC, prevalence, predict-all floor, degenerate flag) |
| `expected/deep_seed_spread.csv` | the seed test's range per deep model, signal and metric |
| `expected/zenodo_checksums.json` | md5 and size of the record's two files |
| `expected/paper/table*.csv` | the article's printed Tables 4 to 16, cell by cell |
| `expected/paper/exceptions.csv` | the 29 cells `make paper` does not reproduce (25 printed, 4 in the two Table 7 rows lost in typesetting), each with its reason |
| `expected/paper/source_only.csv` | the 16 Table 7 cells that are in the article's LaTeX source but not in the typeset article |

`make expected` (`bin/build_expected.py`) regenerates the first five from the fetched artifact.
The printed tables were taken from the article's LaTeX source and each value checked against
the text of the published PDF.

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
  30). The cap is applied inside training, so `best_hyperparams` in `model_results.csv` still
  shows the epoch count the search picked. DAGMM is checked for finite metrics and passed
  through the FDES checks, but not compared numerically, because a reduced search will not land
  on the published number. Recorded runs gave F1 0.793 (venv, Python 3.11.14, August 2026) and
  0.806 (the container, and venvs on Python 3.11.18), with AUC-ROC 0.908 in both. The archived
  25-trial values are 0.877 and 0.939.

In the recorded smoke run (`runs/2026-08-29-smoke-m4pro/`) both classical rows match to four
decimals in both environments. Its `fdes_checks.csv` also shows the section 8b guard firing.
One-Class SVM on logs scores F1 0.565 against a predict-all floor of 0.566 with recall 0.975
and is marked EXCLUDE. Isolation Forest (F1 0.580, recall 0.742) and DAGMM (F1 0.793, AUC
0.908) PASS.

Full mode (`make verify MODE=full`) reads `out/full` and works on any set of folds, one fold
or all five. It fails a retrain only on what is deterministic or measured
(`fdes/retrain_check.py`).

1. Structure. Every requested model and signal has a row for every fold the run holds, and
   each row's test-set size and anomaly count equal the archive's. These do not depend on
   training, so a wrong split, merge or feature table fails here exactly.
2. Isolation Forest and One-Class SVM, per fold, within 0.02 of the archive on precision,
   recall, F1, AUC-ROC and PR-AUC.
3. Deep-model AUC-ROC, per cell, within the archived value plus or minus an allowed gap: the
   seed test's range for that model and signal (at least 0.05), plus 0.05 for the
   hyperparameter search, which a retrain repeats and the seed test held fixed. CNN1D-AE,
   LSTM-AE and LSTM-VAE barely move across folds or seeds and are held to it on every fold.
   Deep SVDD, DAGMM and Transformer-AE change level from fold to fold, so their spread is
   measured only on fold 5, and only fold 5 is held to it. Their other folds are reported
   against the fold 5 gap and not judged.
4. The findings, over the folds the run holds. Among the models run on all three signals,
   exactly CNN1D-AE, LSTM-AE and LSTM-VAE score mean AUC-ROC below 0.5 on every signal, and
   DAGMM has the best mean logs AUC-ROC. On a complete 8 x 3 x 5 run, DAGMM also ranks first
   in the Friedman test on every signal with p < 0.05.

The allowed AUC-ROC gaps:

| Model | Metrics | Logs | Traces | Held to it on |
|---|---|---|---|---|
| CNN1D-AE | 0.106 | 0.140 | 0.177 | every fold |
| LSTM-AE | 0.100 | 0.100 | 0.100 | every fold |
| LSTM-VAE | 0.100 | 0.100 | 0.100 | every fold |
| DAGMM | 0.116 | 0.130 | 0.135 | fold 5 |
| Deep SVDD | 0.224 | 0.425 | 0.787 | fold 5 |
| Transformer-AE | 0.632 | 0.109 | 0.269 | fold 5 |

The wide Deep SVDD and Transformer-AE gaps are what a retrain really does to those models, as
the seed test measured. They are not slack added to make a check pass.

`tests/test_retrain_check.py` runs this check on real retrains shipped in
`supplement/seed_test` and on deliberately broken runs. The ten seed retrains of fold 5, the
archive as a five-fold run, and the archive with each seed run as fold 5 all pass. A fold
under the wrong number, a model at chance, a degraded DAGMM, a classical drift of 0.03, a
missing row, a test set of the wrong size and DAGMM losing its ranking all fail.

`make verify-archive` implements the FDES section 9 requirement that archived tables
regenerate exactly from the archived raw scores. It recomputes `metric_reconciliation.csv`,
Table 4, the below-chance set and the Friedman ranks from the archived scores and per-fold
CSVs and requires equality to three decimals. It runs in about two seconds.

## Runtimes and hardware

| Target | Wall clock | Machine | Notes |
|---|---|---|---|
| `make fetch` | 7 s | Apple M5, 10 cores, 16 GB | both Zenodo files, md5 and unzip |
| `make paper` | 5 min 14 s to 5 min 38 s | Apple M5, 10 cores, 16 GB | rebuild, Tables 4 to 16, Figures 2 to 15. 6 min 50 s on a loaded M3 Pro, 5 min 56 s in the container |
| `make test` | 59 s | Apple M5, 10 cores, 16 GB | 314 tests, the Zenodo download present |
| `make smoke` | 150.4 s (2.5 min) | Apple M4 Pro, 12 cores, 24 GB | logs signal, fold 1. The two classical models take 10.6 s, the reduced-budget DAGMM the rest |
| `make docker-smoke` | 238.7 s (4.0 min) | Apple M4 Pro | arm64 image, verify PASS, record in `runs/2026-08-29-smoke-m4pro/docker/` |
| `make verify` | under 2 s | Apple M4 Pro | |
| `make verify-archive` | 2 s | Apple M4 Pro | |
| `make pilot` | 2 s | Apple M4 Pro | example Isolation Forest plugin |
| `make check` | under 2 s | Apple M4 Pro | sample CSVs in `examples/`, one week at five-minute buckets |
| `make reproduce` | see below | | |

All CPU only, Python 3.11, torch 2.5.1, venv route.

The artifact records 41.8 hours of training time (Optuna search plus final fit, summed over
all 120 model-signal-fold cells) on the article's SageMaker instances, with the deep models
on NVIDIA A10G GPUs. The metrics signal (532k rows, 64 features) accounts for 27.1 of those
hours. On CPU the deep models run several times slower. In the seed test, one deep model on
one fold, all three signals, took 16 minutes (LSTM-VAE) to 4.6 hours (Transformer-AE) on 8
Intel threads, with the article's hyperparameters and no search. `make reproduce` prints an estimate before it starts. It is extrapolated from
timings, not measured on your machine.

## Seed policy and determinism

The wrapper seeds Python `random`, NumPy and PyTorch with 42 + fold id (fold 1 uses 43,
fold 5 uses 47) before each signal and again before each model's Optuna search. A (model,
signal, fold) cell therefore gives the same scores alone as in a full run, so merged slices
match one `make reproduce`. Inside the artifact, Optuna's TPE sampler is seeded with 42,
Isolation Forest uses `random_state=42`, the One-Class SVM subsample uses `RandomState(42)`,
and the baseline (normal-only) split uses `RandomState(42 + fold_id)`, all unchanged. The
article's own run did not seed PyTorch or NumPy (see
[Why a retrain cannot give the article's deep-model digits](#why-a-retrain-cannot-give-the-articles-deep-model-digits)).
The seed test used seeds 1001 to 1010.

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
| CPU against the article's GPU runs | not comparable digit for digit, because the article's run was not seeded. See [What `make verify` compares](#what-make-verify-compares) |

`make paper` does not train, so none of this affects it.

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

   Exit code 0 means PASS, 2 means EXCLUDE under section 8 and 3 means INSUFFICIENT. The pilot
   returns no other verdict codes (5, PASS with a caveat, is `make check` only). Only a failed
   command exits 1, so an
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

These codes are the ones `bin/reproduce.py` returns. `make` cannot pass them on: when a recipe
fails, make prints the code (`make: *** [check] Error 3`) and exits 2 itself, the same number
as EXCLUDE. A pipeline that branches on the verdict has to call `bin/reproduce.py check ...`
directly.

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
bin/reproduce.py        single entry point (fetch, paper, smoke, reproduce, merge, verify, verify-archive, estimate, pilot, check, paper-tables, figures)
bin/build_expected.py   regenerates expected/ from the fetched artifact
fdes/rebuild.py         every per-fold result rebuilt from the archived raw scores, behind `make paper`
fdes/checks.py          FDES section 2, 6, 7, 8 checks
fdes/protocol.py        FDES section 5 procedure for pilots (reuses the artifact's split, cooldown, threshold and metric code)
fdes/tables.py          Table 4, 5, 7, 8, below-chance, Friedman, VUS
fdes/paper.py           the article's Tables 4 to 12 and Figures 2 to 7, and the cell-by-cell verdict
fdes/extra_tables.py    Tables 9 and 13 to 16 from the deposited result files
fdes/figures_extra.py   Figures 8 to 15, drawn by the article's scripts in supplement/
fdes/retrain_check.py   what `make verify MODE=full` holds a retrain to
fdes/vus.py             VUS-PR and VUS-ROC, ported from the TSB-AD reference implementation
fdes/byod.py            the bring-your-own-data path behind `make check`
detectors/base.py       plugin interface; detectors/example_isolation_forest.py
examples/               sample alert, incident and score CSVs plus the script that makes them
tests/                  unit tests (`make test`). Tests that need the Zenodo download skip without it
docs/METHOD.md          what each check does and why
expected/               archived values verify compares against, the article's printed tables, the exceptions list
supplement/             the article's figure scripts and their inputs, and the seed test (not in the Zenodo record)
runs/                   recorded runs: manifest, verify reports, tables (evidence, not inputs)
Dockerfile              python:3.11-slim@sha256:1042b6... + CPU torch 2.5.1 + exact pins
data/, out/             created by fetch and the runs; git-ignored
```

## License

The wrapper code, its documentation and its packaging (`bin/`, `fdes/`, `detectors/`,
`examples/`, `tests/`, `docs/`, `Makefile`, `Dockerfile`, `requirements.txt`, `constraints.txt`,
`CITATION.cff`, `CHANGELOG.md` and this README) are Apache-2.0. The Zenodo artifact fetched
into `data/`, the derived tables in `expected/`, the recorded runs in `runs/` and `supplement/`
are CC-BY-4.0, attribution Mateen Ali Anjum, DOI 10.5281/zenodo.22078287. See `LICENSE`.

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
  version   = {1.5.0},
  publisher = {Zenodo},
  year      = {2026},
  doi       = {10.5281/zenodo.24518271}
}
```

This reproduction package is archived at Zenodo, DOI
[10.5281/zenodo.24518271](https://doi.org/10.5281/zenodo.24518271) (v1.5.0, concept DOI
[10.5281/zenodo.22170201](https://doi.org/10.5281/zenodo.22170201) resolves to the latest version).

The specification is the Failure Detection Evaluation Specification v1.0.0-draft,
https://github.com/mateenali66/failure-detection-evaluation-spec. `CITATION.cff` holds the
machine-readable metadata.

# Changelog

## [1.5.1] - 2026-11-26

### Added

- `supplement/seed_test/runs_folds1-4`, 240 retrains of the six deep models on folds 1 to 4
  under seeds 1001 to 1010, with the article's hyperparameters and 8 threads on three Apple
  M-series Macs, each with its manifest. With fold 5 the seed test covers every fold, 300
  retrains in all.
- `supplement/seed_test/cross_processor_m5`, the fold 5 DAGMM and LSTM-AE jobs under seed
  1001 on an M5. They differ from the same jobs on Intel and repeat exactly on the same M5.

### Changed

- `make verify MODE=full` judges every deep model on every fold against that fold's seed
  spread. Before, Deep SVDD, DAGMM and Transformer-AE were judged on fold 5 only, and the
  other three on every fold against the fold 5 spread.
- Each gap's span includes the article's value, so measured seeds still pass where the
  article lies outside them (DAGMM traces fold 3 by 0.110, LSTM-AE traces fold 4 by 0.044).
  Two fold 5 gaps grow, DAGMM logs from 0.130 to 0.155 and LSTM-AE traces from 0.100 to 0.113.
- A run of one fold reports the below-chance finding without judging it. Two of the 50
  single-fold seed retrains break it, and all 260 runs of two to five folds keep it.
- A deep-model cell with no declared spread fails instead of being reported.
- `expected/deep_seed_spread.csv` and `supplement/seed_test/seed_spread.csv` have a fold
  column and keep four decimals, and `article_f5` is renamed `article`.
  `supplement/seed_test/analyse_seeds.py` reads the runs of every fold.
- The README describes the seed test on every fold, gives the allowed gaps per fold, and adds
  the two-processor comparison and the run times on the Macs.

## [1.5.0] - 2026-11-24

### Added

- `make paper`. It rebuilds every per-fold result the article reports (metrics with and
  without cooldown, late fusion, per-fault recall) from the archived raw scores with the
  artifact's own `training.py` functions, checks each value against the archived CSVs, builds
  Tables 4 to 16 and Figures 2 to 15, and compares every printed cell with the article. It
  exits 0 only when each printed cell matches or is listed with its reason in
  `expected/paper/exceptions.csv`. On a clean machine, macOS or the Linux container, 550 of
  the 575 printed cells match and 25 are listed. Sixteen cells of Table 7 that are in the
  article's LaTeX source but not in the typeset article (`expected/paper/source_only.csv`) are
  compared and counted apart. It needs no training.
- Tables 9 and 13 to 16 (`fdes/extra_tables.py`), built from the deposited result files with
  the artifact's own summary functions, and their printed values in `expected/paper/`.
- Figures 8 to 15 (`fdes/figures_extra.py`), drawn by the article's own scripts from the
  article's recorded outputs, which are added in `supplement/`. The separability analysis
  behind Figures 8 to 11 is also run again on the deposit and compared with those outputs.
- `make fetch` also downloads the record's second file, the external-validity results behind
  Table 16, and checks its md5.
- `constraints.txt` pins every package the nine pinned requirements pull in. The Dockerfile
  installs torch alone from the CPU index and everything else from PyPI under these pins, so
  the container and a venv get the same versions.
- `supplement/seed_test`: the six deep models retrained on fold 5 with the article's
  hyperparameters under seeds 1001 to 1010, with the script, the per-run manifests and
  `expected/deep_seed_spread.csv`.
- `make merge`. It runs the existing `merge` subcommand inside the image like the other
  targets. The README told users to call it with the host Python, which fails on the Docker
  route without the dependencies.

### Changed

- `make verify MODE=full` works on any set of folds and checks what a retrain can be held to
  (`fdes/retrain_check.py`): exact test-set sizes and anomaly counts, the classical models per
  fold within 0.02, deep-model AUC-ROC per cell within a gap taken from the seed test, and the
  article's findings. Before, it compared five-fold means with tolerances that an honest
  retrain could fail, and it failed every run with fewer than five folds.
- Tables 8 and 11 print the Deep SVDD and DAGMM standard deviations as the article did
  (population standard deviation), and Table 10 rounds as the article did (half to even).
  The sample-SD builds are written beside them.
- The README explains why a retrain cannot give the article's deep-model digits: the archived
  training code does not seed PyTorch or NumPy. It used to name CPU against GPU as the cause.
- Figure 6 shows the six models the article plots. `fig6_per_fault_recall_heatmap_all_models`
  adds Deep SVDD and DAGMM.
- `make paper` builds Table 11's Random Forest rows from the article's deposited results. The
  seeded Random Forests reproduce them exactly on macOS and differ by up to 0.003 per fold on
  Linux, so they are trained again and the gap is reported instead of judged.
- `make` runs on `venv/bin/python` (or `venv/Scripts/python.exe` under Git Bash) when that venv
  exists, so the Quick start works without activating it. The README says that `make` turns
  every failing exit code into its own 2.
- `make paper` writes `paper_manifest.json` (package version, Python and library versions,
  machine, md5 of the Zenodo files read), and `comparison.csv` marks each cell as printed or
  LaTeX-source-only and gives the reason for each listed one, so it reproduces the report's
  counts on its own. Run manifests, `verify_report.txt` and `pilot_result.json` record the
  package version, and run manifests the library versions.
- `make help` prints the Makefile's header from its first line.

### Fixed

- Running the full reproduction one fold at a time, as the README shows (`make reproduce
  FOLDS=1`, then `FOLDS=2` and so on), left `out/full/results` holding only the last fold.
  Each call merged only the folds it had trained, so it replaced the results of the calls
  before it. `make paper-tables`, `make figures` and `make verify MODE=full` then worked from
  one fold, printed no standard deviations, and compared a single fold with the article's
  five-fold means. Each call now merges every `out/full/fold<k>` directory, and leaves out
  any that holds a model or signal outside the current call. The fold directories themselves
  were always complete, so a run made this way with 1.4.1 can be rebuilt with `make merge`,
  without training.
- `make paper-tables` skips a table, with the reason, when an analysis script it needs is not
  present, instead of stopping with an import error.
- No bytecode is written into the fetched artifact under `data/`. `rebuild_differences.txt` is
  written only when the rebuild differs from the archive.
- Every target except `make fetch` stops with one line naming the missing packages and the
  two ways to install them, when the Python it runs on lacks the requirements. Before, a failed
  `make docker-build` followed by `make paper` ended in a `ModuleNotFoundError` traceback.

## [1.4.1] - 2026-10-10

### Changed

- README, `docs/METHOD.md`, this changelog and the code comments are rewritten to state what
  the code does and why, without the background to each change. The README is 677 lines, down
  from 713, `docs/METHOD.md` 539, down from 697.
- Service names taken from real exports are replaced with placeholders in the test fixtures
  and the documentation.
- The METHOD worked example uses the command that produces the output shown.
- The two recorded reports under `runs/2026-08-29-smoke-m4pro/` give paths relative to the
  repository instead of absolute local paths. Their results are unchanged.

### Unchanged

No code path changed. Results are identical to 1.4.0, and all 264 tests pass.

## [1.4.0] - 2026-10-10

### Fixed

- Pinned scikit-learn 1.6.1 instead of 1.5.2. The archived results were produced under 1.6 or
  later, and 1.5.2 builds a different Isolation Forest on metrics and traces for the same seed
  and hyperparameters. Logs is the same under both, which is why the logs-only smoke run always
  passed. Under 1.6.1 all 30 classical cells (Isolation Forest and One-Class SVM, 3 signals,
  5 folds) match the archived raw scores to float rounding, worst case 4.6e-6. The likely cause
  is the tree splitter rewritten in scikit-learn 1.6 for missing-value support (#27966, #30318).

- Every Python target now runs inside the image once it is built. Only `docker-smoke` used it
  before, so the other targets ran on the host Python and failed without its dependencies.
  `NO_DOCKER=1` runs them locally. The Makefile uses `$(CURDIR)` instead of `$(PWD)`, which Git
  Bash leaves unset, falls back from `python3` to `python`, and turns off MSYS path conversion.

- Removed the `VOLUME` line from the Dockerfile. With the checkout bind-mounted at `/work`,
  Docker still created anonymous volumes at `/work/data` and `/work/out`, so the download and
  all results were lost when the container exited. Both directories are now mounted explicitly.

- The archived figure scripts cannot draw the article's figures. `code/figures/*.py` in the
  Zenodo record are six-model versions that read `results_v3/merged`, which the archive does not
  contain. `make figures` and `make paper-tables` rebuild Figures 2 to 7 and Tables 4 to 12
  (Table 9 aside) from one results directory.

- The README mapped Tables 10, 11 and 12 to the wrong files. Table 12's right column pointed at
  `model_results_with_cooldown.csv`, which holds per-fold rows, and Table 11's dependence on
  `supervised_baseline.py` was not mentioned. The map now says how each table is built.

- Deep SVDD and DAGMM labels were 29 windows off their scores. The archived `training.py`
  shifts labels for sequence models from a hardcoded list in two places, the saved label files
  and late fusion, and the two models were never added to it. In-pipeline metrics were
  unaffected. Tables 10 and 12, which are recomputed from the saved files, move by at most 0.001
  in AUC and 0.004 in PR-AUC. The wrapper patches both lines at import, leaves the archived file
  unchanged, and refuses to run if either line is missing.

- Each model's Optuna search now starts from seed 42 + fold id. Seeds were reset once per
  signal, so a model's numbers depended on which models ran before it in the same process, and
  `MODELS=DAGMM` differed from a full run. A cell now gives the same scores alone or in a full
  run, so slices can be merged.

- Thread count and x86 instruction set are pinned. PyTorch, MKL, oneDNN, OpenBLAS and NumPy
  chose kernels and thread splits from the host CPU. Threads are fixed at 8 and the instruction
  set at AVX2, oneDNN and NumPy's AVX-512 loops are off, and the settings go into
  `run_manifest.json`. Repeat runs are bit-identical, and so are Intel Sapphire Rapids and Intel
  Ice Lake. Intel and AMD still differ for the deep models, because `sqrt` (used by Adam) and the
  Cholesky factorisation in DAGMM differ between the two vendors under every MKL setting tried.

- `make test` failed after the README was split, because a test still looked in the README for
  the section moved to `docs/METHOD.md`. The README section where incident files are built had
  also lost its pointer to that section. Both are fixed.

- `make fetch` retries five times with backoff and points at `FROM_ZIP=` when it gives up. A
  single Zenodo 502 used to end it.

### Added

- `make paper-tables` and `make figures`. Every printed cell is compared with `expected/paper/`.
  `RESULTS=archive` checks the article against its own archive in seconds, without training.
- `bin/reproduce.py merge` joins slices run with `FOLDS=` or `MODELS=`. Each slice must cover
  all three signals so its fusion rows exist, and a cell present in two slices is refused.
- A README section, Known differences from the article, listing where the article's printed
  tables differ from its archive.

### Unchanged

No archived file was modified. `make verify-archive` still passes.

## [1.3.9] - 2026-09-04

### Fixed

- A run where no check can fail is now INSUFFICIENT. Above a prevalence of 0.5 the
  flag-everything guard's bar exceeds an alerted rate of 1.0, section 8b needs recall at or
  above 0.95, and alerts mode has no rank metric for section 8a. At a prevalence of 0.853 the
  predict-all floor is 0.92, and a detector alerting on 88 percent of the timeline returned PASS
  at exit 0 with no check firing. The verdict is withheld only when the guard is unreachable and
  the lift sits inside the near-floor band. A detector clearly above the floor keeps its PASS,
  and an EXCLUDE always stands.

- The duplicate notice said duplication multiplies the alerted rate. It does not, because
  occupancy is a union and duplicates claim buckets that are already claimed. Only alerts per
  incident moves. Introduced in 1.3.8.

- Duplicate detection missed jittered copies. Delivery fan-out produces copies a second or two
  apart, so exact-pair matching found none in 744 rows while alerts per incident was inflated
  fourfold. The check now also compares the row count with the number of separate stretches the
  rows cover. It is worded more weakly than exact duplication, because a bursty detector
  produces the same shape.

- Windows that share a bucket were reported as overlapping in time. Twelve incident rows 1500
  seconds apart were described as overlapping although none touch. Overlap is now measured on
  raw times, windows that only share a bucket are reported as an effect of the bucket size, and
  genuine overlap is reported in seconds.

### Unchanged

No archived result moved. `make verify` and `make verify-archive` both return PASS, and
`fdes_checks.csv`, `table4_single_signal.csv`, `vus.csv` and `pilot_report.md` are byte
identical to the reference run. All six bundled examples keep their documented exit codes.
231 tests pass.

## [1.3.8] - 2026-09-04

### Fixed

- The check table printed "pass" for a flag-everything guard that cannot fire. The bar is the
  square root of twice prevalence, so above a prevalence of 0.5 it exceeds 1.0. At a prevalence
  of 0.853 the table showed a bar of 1.306 next to "pass". It now reads "not reachable at this
  prevalence", and `alert_rate_bar_unreachable` records it.

- The sweep prose named a single mechanism for verdicts that change with the bucket. It said a
  coarse bucket raises recall while barely charging precision, but a verdict can also flip
  because the alerted rate crosses the guard bar while the lift stays above the floor. The sweep
  table now has a Guard bar column and a Decided by column naming the check behind each row,
  and the prose describes both mechanisms. The near-floor notice no longer says the floor is why
  a verdict is UNSTABLE.

- The sweep says when prevalence moves across the bucket ladder. A coarse bucket raises
  prevalence as well as recall, and both the predict-all floor and the guard bar are computed
  from prevalence. It is named when prevalence moves by `PREVALENCE_DRIFT_NOTICE` (2.0) times or
  more.

- A prevalence above 0.5 is reported at the top. It usually means the incident file records
  ticket lifetime rather than incident time. The notice also says that it disables the
  flag-everything guard.

### Added

- Duplicate alert rows are detected and reported. An alert delivered to several recipients
  appears once per recipient with the same timestamp, which inflates alerts per incident. Every
  row is still counted, because a fan-out cannot be told apart from a detector that fired twice,
  and the report says what it found. An even multiplicity across every distinct row is named as
  the signature of a fan-out.
- `check_result.json` records the exit code. Before, it had to be re-derived from the verdict
  and two flags, which went wrong on a suppressed UNSTABLE.
- `--help` documents exit 5.
- The alerted rate is a top-level field in `check_result.json`. It was only inside
  `degenerate_output`.
- The alerts-per-incident notice prints its denominator and says it is counted on raw incident
  times, so it can differ from the stretch count in the coverage section.

### Unchanged

No archived result moved. `make verify` and `make verify-archive` both return PASS, and
`fdes_checks.csv`, `table4_single_signal.csv`, `vus.csv` and `pilot_report.md` are byte
identical to the reference run. 226 tests pass.

## [1.3.7] - 2026-09-04

### Fixed

- The alerts-per-incident denominator depended on the bucket size. 1.3.6 merged incident windows
  with the bucket size as the gap tolerance, so twelve incident rows counted as twelve stretches
  at a five minute bucket and six at an hourly one. The sweep computes the denominator once and
  applies it to every row, so the whole sweep table moved with the bucket passed, and the same
  two files gave a PASS headline at one bucket and UNSTABLE at another with identical printed
  numbers. The tolerance is now zero, so only windows that touch or overlap are merged. On the
  case that motivated merging, forty rows still collapse to two stretches at every tolerance
  from zero to an hour.

### Changed

- The alerts-per-incident limit is described as a floor against absurd volumes. The notice says
  that a run at 49 and one at 50 are not meaningfully different.
- The notice says its denominator is stretches merged from the supplied rows, and that it moves
  with the assumed incident length. On one export, capping incident windows at an hour changed
  the ratio seventeenfold without changing the decision to report it.
- Scores mode states that it cannot measure alert volume. A score series has no discrete alert
  windows. Threshold up-crossings would be the equivalent and are not implemented, because with
  an in-sample threshold the count would inherit the tuning.

### Known and not fixed

- A detector at 49 alerts per incident can still pass when it should not. The denominator is a
  count, so twelve five-minute incidents allow as many alerts as twelve five-hour ones.
  Normalising by incident duration was tested and swung by a factor of thirty-eight against
  seventeen, because it carries the incident-length uncertainty twice, and it reduces to
  prevalence, which the guard already uses.

### Unchanged

No archived result moved. `make verify` and `make verify-archive` both return PASS, and
`fdes_checks.csv`, `table4_single_signal.csv`, `vus.csv` and `pilot_report.md` are byte
identical to the reference run. 219 tests pass.

## [1.3.6] - 2026-09-04

### Fixed

- Alerts per incident depended on how the incident file was formatted. Row count reflects how a
  tracker records an outage, whether one row per incident, per affected service or per status
  update. The same two one-hour incidents written as forty three-minute rows moved the ratio
  from 355 to 17.75 and flipped the verdict, with every other number unchanged. The denominator
  now counts merged incident stretches, joining windows closer together than the bucket size.

- `pass_qualified` was true on runs that did not pass. It was set from the caveats alone, so
  every EXCLUDE carried it. Introduced in 1.3.5.

- `pass_qualified` was only nested under `results`. It now sits next to `verdict`.

- Exit 5 was undocumented. The README listed 0, 2, 3 and 4, so a CI job written from it failed
  on a passing detector with caveats. The README now has an exit-code table and says that
  whether 5 fails a build is the reader's decision. The `--no-sweep` description now says it
  holds the reported verdict and not the exit code.

### Changed

- The volume notice gives alerts per day and per eight-hour shift. It says an alert window is
  not necessarily a page, since alerting stacks deduplicate and group. Bucket-level precision
  is no longer presented beside the volume as if it were the share of useful pages, and the
  notice says at most one window per incident stretch can be a first page.
- A volume within 10 percent of the limit says so.
- The README describes `SUPPRESSION_MAX_LEVERAGE` as a floor against malformed exports. The
  ratio it caps is roughly the bucket divided by the window, so it is measured in the same
  quantisation it polices, and it has only bound on two degenerate exports, at 300 and 370.

### Known and not fixed

- The limit of 50 is a step, so a detector at 49 can pass when it should not. One example is
  588 alerts against 12 incidents at precision 0.020, which is 19.6 pages a day. Short incidents
  buy the same alert budget as long ones. Merged stretches fix the formatting problem and leave
  this one.
- Alert volume is not measured in scores mode, because a score series has no discrete alert
  windows and threshold up-crossings are not implemented.
- The value 50 rests on four reference points, one of them 1.1 percent under it.

### Unchanged

No archived result moved. `make verify` and `make verify-archive` both return PASS, and
`fdes_checks.csv`, `table4_single_signal.csv`, `vus.csv` and `pilot_report.md` are byte
identical to the reference run. 218 tests pass.

## [1.3.5] - 2026-09-04

### Fixed

- The alert-volume gate is a ratio, `ALERTS_PER_INCIDENT_LIMIT` (50), instead of a rate per
  clock hour. The per-hour gate from 1.3.4 had a step (720 alerts in a month passed and 726
  excluded, with every other measure identical to three decimals), and averaging hid
  clustering, since three alerts every third hour averages 0.99 an hour. The ratio orders the
  known cases correctly, at 31 for a useful detector and 120 and 355 for two that are not.

- Alert volume is reported on its own, outside the suppression gate. A detector can clear the
  guard relative to prevalence and still alert 174 times a day at precision 0.27, and no check
  measured that. Above the limit the report gives alerts per incident, says no other check
  measures it, and leaves the judgement to the reader.

- A PASS with a caveat sets `pass_qualified` and exits 5. This applies when the PASS exists
  because an exclusion was suppressed, or when the detector alerts far above the volume limit.
  Whether 5 fails a build is the reader's choice.

- Usage errors exit 1. argparse exits 2 on a bad flag, which is this tool's code for EXCLUDE,
  so a mistyped flag read as an EXCLUDE verdict.

- The dominant-alert-window check needs `DOMINANT_ALERT_MIN_ROWS` (five) rows. With two or three
  rows one of them owns most of the alerted time by arithmetic.

- The scope assumption line names both directions, alerts on scopes with no incident (false
  positives) and incidents on scopes with no alert (false negatives).

### Unchanged

No archived result moved. `make verify` and `make verify-archive` both return PASS, and
`fdes_checks.csv`, `table4_single_signal.csv`, `vus.csv` and `pilot_report.md` are byte
identical to the reference run. All six bundled examples keep their documented exit codes.
213 tests pass.

### Known and not fixed

- `ALERTS_PER_INCIDENT_LIMIT` is still a constant with a step at 50. It sits between the only
  reference points available, 31, 120 and 355.

## [1.3.4] - 2026-09-04

### Fixed

- The quantisation suppression is gated on alert volume. The 1.3.3 gate counted only
  zero-length rows, so one-second windows passed it, and 5,184 of them (173 pages a day,
  precision 0.014, all four buckets agreeing) came back PASS at exit 0. That detector has a
  lower duty cycle than a legitimate one, 0.0020 against 0.0125, so no gate based on duty cycle
  or window length can separate them, while alert count separates them by 28 times. Two gates
  now apply together. `SUPPRESSION_MAX_LEVERAGE` (50) caps how much of the alerted rate the
  suppression may attribute to the bucket (20 for the legitimate case, 300 for the high-volume
  one), and `SUPPRESSION_MAX_ALERTS_PER_HOUR` (1.0) caps alert count. Both are tested at their
  crossing point.

- The check table printed `pass` for a guard that had stood down. It now says "not applied, see
  the notice above".

### Unchanged

No archived result moved. `make verify` and `make verify-archive` both return PASS, and
`fdes_checks.csv`, `table4_single_signal.csv`, `vus.csv` and `pilot_report.md` are byte
identical to the reference run. 209 tests pass.

### Not taken

- Returning exit 2 whenever the suppression fires. With the suppression gated on
  `SUPPRESSION_MAX_LEVERAGE` and alert volume, a suppressed run is quiet, and failing it in CI
  would fail a working detector. The exit code follows the verdict.

## [1.3.3] - 2026-09-04

### Fixed

- The bucket sweep did not apply the quantisation suppression. The headline verdict did, so a
  report could show a PASS headline above a sweep row for the same bucket saying EXCLUDE, and
  then call the run unstable. The duty cycle does not depend on the bucket, so it is computed
  once and passed into every row.

- A single durational row defeated the quantisation suppression. `duty_cycle` skipped
  zero-length windows and gave up only when every window was instantaneous. 5,184 point events
  plus one ten-minute window gave a duty cycle of 0.0002 while alerting on 60 percent of
  buckets, which suppressed the guard and passed a detector paging 173 times a day at precision
  0.014. When more than `ZERO_LENGTH_DUTY_LIMIT` (0.20) of in-range rows are zero-length, the
  duty cycle covers only a subset of the detector, so the suppression is not applied.

- The scope overlap check looked only at alert rows. Incidents on scopes that no alert covers
  are guaranteed false negatives and were not reported, though 74 percent of incidents were in
  that case on one export. `poor_alert_overlap` and `poor_incident_overlap` are now separate,
  `poor_overlap` is either of them, and the incident direction has its own notice.

- `low_recall` and `alerted_rate_ratio_high` read false in the JSON when they were true. Each
  combined "is this true" with "should this be printed", so a run with recall 0.029 recorded
  `low_recall` as false when another exclusion reason was present. The fact and the decision to
  print are now separate fields, the second with `_notice` in its name.

### Added

- A dominant alert window check. Above `DOMINANT_ALERT_SHARE` (0.30) of alerted time in one
  window, the report names the window and says the usual cause is an alert still firing when
  the export was taken.
- The tool version in the report, next to the specification version. A test checks that
  `TOOL_VERSION` matches `CITATION.cff`.
- A caution where the report quotes scope names, since they come from the user's files and can
  contain account or subscription identifiers.

### Changed

- `--scope-col` help says it reports and never filters.

### Unchanged

No archived result moved. `make verify` and `make verify-archive` both return PASS, and
`fdes_checks.csv`, `table4_single_signal.csv`, `vus.csv` and `pilot_report.md` are byte
identical to the reference run. 205 tests pass.

### Still open

- No guidance for an alert still firing at query time. The dominant-alert notice names the
  symptom only.
- No warning when summed incident-window time exceeds the observation range. The overlap
  fraction is reported and the multiple is not.
- In scores mode `--bucket` defaults to the median spacing, which is undocumented for irregular
  series.

## [1.3.2] - 2026-09-04

### Fixed

- A coarse bucket could exclude a detector that alerts briefly and often. The alerted rate is
  bucket occupancy, so at a coarse bucket a one-minute alarm claims the whole bucket. Six
  one-hour incidents over thirty days, all caught, plus six one-minute false alarms a day passed
  at 1m, 5m and 15m and were excluded at 1h with a report saying the detector alerted 30 times as
  often as anything was wrong, which overstated it by a factor of twenty. Before excluding, the
  guard's rule is now applied to the raw alert and incident durations, and if it does not fire
  there the exclusion comes from the bucket size and is not applied. The notice gives the duty
  cycle, the true ratio, the alert count and the precision. `check_result.json` gains
  `alert_duty_cycle`, `incident_duty_cycle`, `alert_rate_quantised` and `alert_windows`. Alerts
  mode only.

- The ratio notice contradicted the quantisation notice on the same number. It is suppressed
  when the quantisation notice applies.

### Added

- An exclusion within `NEAR_BAR_BAND` (0.10) of the bar names the margin and says a small change
  in either file could move it.

### Unchanged

No archived result moved. `make verify` and `make verify-archive` both return PASS, and
`fdes_checks.csv`, `table4_single_signal.csv`, `vus.csv` and `pilot_report.md` are byte
identical to the reference run. All six bundled examples keep their documented exit codes.
198 tests pass.

## [1.3.1] - 2026-09-04

### Fixed

- Two notices contradicted each other. The alert-volume counterweight from 1.3.0 fired on any
  lift above 1.0, while the near-floor notice covers `NEAR_FLOOR_BAND` (0.20) either side of
  1.0, so a lift between 1.0 and 1.2 printed both "the detector is finding incidents" and "this
  detector scores about what flagging every bucket would score". The counterweight is now
  suppressed inside the near-floor band, reusing the same constant so the two cannot overlap.
  The tests sweep the lift through the band.

- `git clone --depth 1 --branch v1.3.0` printed `warning: refs/tags/v1.3.0 ... is not a
  commit!`. That is git's shallow-clone handling of an annotated tag. Release tags are
  lightweight from 1.3.1 on.

### Unchanged

No archived result moved. `make verify` and `make verify-archive` both return PASS, and
`fdes_checks.csv`, `table4_single_signal.csv`, `vus.csv` and `pilot_report.md` are byte
identical to the reference run. 195 tests pass.

## [1.3.0] - 2026-09-04

### Changed

- The flag-everything guard is one continuous curve. It fires when the alerted rate squared
  reaches `ALERT_RATE_PRODUCT` (2) times prevalence, replacing `ALERT_RATE_SATURATION`,
  `ALERT_RATE_RATIO_FLOOR` and `ALERT_RATE_RATIO_MULTIPLE`, and `ALERT_RATE_MULTIPLE` is now only
  the ratio worth printing. The two-path guard in 1.2.0 had flat regions where a floor decided
  alone, so at a prevalence of 0.002 an alerted rate of 0.1989 passed and 0.2009 excluded. The
  curve passes through the old anchors (0.20 at a prevalence of 0.02, 0.50 at 0.125) and is
  stricter everywhere else, so verdicts only move towards EXCLUDE, and none of the 120 archived
  folds changes. A perfect detector cannot trigger it, because that would need a prevalence of
  2. `check_result.json` gains `alert_rate_bar`, and `alert_rate_path` is now `"curve"` or null.

- `--no-sweep` can no longer turn a failing run into a passing exit code. A suppressed UNSTABLE
  exits 4, and the flag still holds the reported verdict. `check_result.json` carries
  `sweep_suppressed_unstable`.

- An exclusion on alert volume says so. When the guard fires on a detector with lift over the
  floor or high recall, the exclusion names both and says the detector is excluded on alert
  volume, which is a judgement about the reader's on-call load.

- The exclusion sentence names the alerted rate, the bar at that prevalence and the ratio,
  without nested clauses.

### Corrected

- The 1.2.0 entry described a detector alerting on 40 percent of a fourteen day timeline at a
  prevalence of 0.0397 as measured. It matches neither export and is a constructed case, built
  in the test suite at 57 anomalous buckets of 1440. The 1.2.0 entry carries a note, and the
  README no longer makes the claim.
- The README known-limit paragraph described floors that no longer exist. It now gives the
  measured values and says the bar sits below them.

### Fixed

- The first version of the exit-code change was applied at the wrong nesting level and did
  nothing. The end-to-end test added with it caught this.

## [1.2.0] - 2026-09-04

### Changed

- The alerted-rate guard gained a second path. The original rule, `rate >= 0.5 AND rate >= 4 x
  p`, behaved as one condition, because four times prevalence is below 0.5 whenever prevalence
  is under 0.125. A second path fires at `rate >= 0.20 AND rate >= 10 x p`. The 0.20 floor keeps
  a rare-incident detector that alerts on 1 percent of the time at a prevalence of 0.001
  passing, and at full recall the multiple of 10 means fewer than one alert in ten lands on an
  incident. In a constructed case at a prevalence of 0.0397, a detector alerting on 40 percent
  of the timeline passed, and a rate of 0.499 passed while 0.500 excluded (marked as constructed
  in 1.3.0, after this entry first described it as measured). `ALERT_RATE_RATIO_FLOOR` and
  `ALERT_RATE_RATIO_MULTIPLE` in `fdes/checks.py` set the path, `check_result.json` records it
  under `alert_rate_path`, and no archived verdict changes.
- `--no-sweep` reports a verdict it suppressed. The sweep always runs. The flag holds the
  verdict and exit code at the bucket passed, and the report says at the top that it suppressed
  an UNSTABLE and what the exit code would have been. `check_result.json` carries
  `sweep_suppressed_unstable` and `bucket_sweep.applied`.
- An empty scope intersection with a single scope on one side is reported as a naming
  difference. Datadog's `service` tag holds application names, while PagerDuty's `service` field
  can be a single routing destination, so the intersection is empty by construction. The report
  says the files use two naming schemes and suggests taking the incident scope from the alert
  payload or the incident title, and it no longer suggests filtering both files to shared
  scopes, which would leave them empty. `SCOPE_NAMESPACE_MAX_SCOPES` in `fdes/byod.py` sets the
  threshold, and `results.scope` gains `empty_intersection`, `namespace_mismatch`,
  `single_scope_side` and `single_scope_name`.

### Added

- The ratio of alerted rate to prevalence, printed next to the verdict when it reaches four
  times prevalence and the guard did not fire, with the floor that kept the guard quiet. On two
  real datasets it ran from 11.7 to 14.7 while the guard never fired. It also appears in the
  check table, and `check_result.json` carries `alerted_rate_over_prevalence` and
  `alerted_rate_ratio_high`.
- A thin-recall notice when a PASS has recall under 0.5. The procedure has no minimum recall,
  as the README explains, and one run passed at recall 0.071 having missed 93 percent of the
  incident time. `LOW_RECALL_NOTICE` in `fdes/byod.py` sets the band, and `check_result.json`
  carries `low_recall`.
- A README section, "Where the incident windows came from", on circular ground truth, and a line
  in every report's assumptions saying provenance was not checked. Incident windows built from
  the detector's own alerts give near-perfect recall.
- A README known limit noting that the guard's absolute floors sit above where measured
  detectors landed, so the ratio informs and the floors exclude.
- A README section on exporting alert data, with Datadog Watchdog specifics. Watchdog output is
  in Events rather than Logs, the Events API v2 query is `filter[query]=source:watchdog`,
  windows are rebuilt by grouping on `story_key` and taking the minimum and maximum timestamp
  (the end is the last update, so it only approximates resolution), pagination truncates
  silently, and `has_notification` separates alerts that reached a person. It also notes that
  tracker close times reflect ticket handling rather than impact.
- `--scope-col` and `--incident-scope-col`, with automatic recognition of `service`,
  `service_name`, `scope`, `entity` and `component`.
- `--no-sweep`, which turns off the bucket sweep.
- A fourth verdict, `UNSTABLE`, exit code 4, when the verdict changes across bucket sizes.
  Alerts mode only.
- `check_result.json` gains `incident_concentration`, `bucket_sweep` and `scope` under
  `results`, and `scope_column` and `distinct_scopes` under each window file in `inputs`.
- `check`, a bring-your-own-data path that evaluates a detector against your own monitoring
  output with no download. It takes alert windows or per-timestamp scores as CSV, with incident
  windows as ground truth. `make check` runs it on the bundled examples.
- `examples/`, sample CSVs including a deliberately useless detector.
- A second reading of FDES section 8b on the alerted rate, `alert_rate_saturated()` in
  `fdes/checks.py`, applied by `check` as `alert_rate_far_above_prevalence`. It fires when the
  detector alerts on at least half the time and at least four times as often as anything is
  anomalous.
- A near-floor notice when the lift is within 20 percent of 1.0, saying the result is not stable
  and suggesting other bucket sizes. `check_result.json` carries `near_floor`.
- A point-events notice when a window file is read as instants because no end column was
  recognised. It names the file's columns and the flag that fixes it, and `check_result.json`
  records every window file's columns under `inputs`.

### Fixed

- A doubled full stop in the mixed-timezone assumption line.
- Zero-length windows are accepted. A row that starts and ends at the same second is an
  instantaneous event, and refusing them rejected real exports, such as Datadog Watchdog stories
  with a single event (9 of 157 rows in one export). They mark the bucket that contains them and
  are listed in the assumptions. A row that ends before it starts is still refused.
- Forgotten open tickets could dominate prevalence without a warning. In one export of 23
  incidents over 35.6 days, two rows open for 13 and 12 days held 601 of 620 incident-hours, and
  prevalence came out at 0.383 instead of 0.020. A row is now flagged as implausibly long when it
  covers at least 10 percent of the observation range and runs at least 10 times the median
  incident, given at least three in-range rows. The report names the rows, their share of
  incident time and the prevalence without them, and nothing is dropped. When the longest tenth
  of rows owns half or more of incident time, the report calls the ground truth lopsided.
  `LONG_INCIDENT_RANGE_SHARE`, `LONG_INCIDENT_MEDIAN_MULTIPLE` and `CONCENTRATION_NOTICE` set the
  thresholds, and the rows are listed under `incident_concentration`.
- The bucket size could decide the verdict. On one export the verdict was EXCLUDE at 1m and 5m
  and PASS from 15m to 2h, with the lift rising from 0.82 to 1.28, because a coarse bucket lets
  one short alert cover a whole incident bucket. Alerts mode now runs the check at 1m, 5m, 15m
  and 1h plus the `--bucket` passed. If the verdicts agree it stands, and if they disagree the
  verdict is `UNSTABLE` and the report leads with the table. INSUFFICIENT rows are listed and
  not compared. Scores mode has no sweep, because there the operating point moves with the
  bucket. `SWEEP_BUCKETS` in `fdes/byod.py` sets the ladder.
- Alert and incident files describing different systems went unnoticed. Both CSVs can carry an
  optional scope column, and the report gives the scope counts on each side, how many are
  shared, and the share of alert rows on scopes with no incident, warning at half or more. Scope
  is reported and never used to filter, and the assumption list says when it was not checked.
  `SCOPE_OVERLAP_POOR` sets the threshold.
- A flag-everything detector reported PASS. A detector alerting on 94.5 percent of a week
  (prevalence 0.043, recall 1.00, precision 0.045) had F1 0.087 against a floor of 0.082, 5.6
  percent above it, so the section 8b margin of 5 percent did not fire. A second reading of
  section 8b now compares the alerted rate with prevalence and fires at an alerted rate of at
  least 0.5 and at least four times prevalence. A perfect detector cannot meet the second
  condition at any prevalence. The guard applies on the `check` path only and fires on none of
  the 120 archived folds.
- A lift within 20 percent of 1.0 gets its own line next to the verdict. On one export the lift
  was 1.17, 1.06, 1.05 and 1.02 across four buckets and the verdict flipped. `NEAR_FLOOR_BAND`
  sets the band.
- Reading every row as a point event is reported next to the verdict. A Splunk export carries
  `_time`, `_indextime`, `earliest` and `latest`, and the tool recognised `_time` as a start and
  no end, which turned a PASS into an EXCLUDE at three bucket sizes. The notice names the file,
  the start column used and all of the file's columns, and points at `--end-col` or
  `--incident-end-col`.
- An empty file was described by its rows instead of its header. The schema is now read from
  the header.
- The README and the `--help` text for the tool, `check` and `pilot` document the exit codes,
  0 for PASS, 2 for EXCLUDE, 3 for INSUFFICIENT and 1 for a real failure. No verdict shares a
  code with an error. `check` exits through one `fail()` helper for every input problem, and a
  test keeps the codes apart.
- A single-class fold reported PASS on the pilot path. `checks_from_scores()` sets AUC-ROC and
  PR-AUC to nan on one class, and `nan <= 0.5` is False, so section 8a never fired. `check_row()`
  now reports each check as passing, failing or `not evaluable`, a single-class fold is
  `INSUFFICIENT`, and a fired exclusion still outranks an unevaluable check. `pilot_result.json`
  carries `check_status`, and `make pilot` exits 3 on `INSUFFICIENT`. Every archived fold has
  both classes, so no published result changes.
- `--start-col` and `--end-col` were applied to the incidents file as well.
  `--incident-start-col` and `--incident-end-col` now cover it and fall back to the alert flags
  when not given.
- The recorded pilot report under `runs/` predated the VUS row and was regenerated. A test
  compares it with what the renderer writes.
- An unevaluable run reported three passes. With prevalence zero, an F1 of zero sits on the
  floor, so three guards printed `pass`. Each check now reports one of three states, and
  `check_result.json` carries `check_status`.
- `EXCLUDE` covered both a worthless detector and unusable ground truth. A third verdict,
  `INSUFFICIENT`, exit code 3, covers the input cases, which are prevalence zero, no incident
  inside the range, an empty incident CSV, one class in every bucket, or every alert window
  outside the range. The reason is printed at the top. An alert CSV whose rows all fall outside
  the range is now `INSUFFICIENT`, and one with no rows is still `EXCLUDE`.
- Overlapping alert windows were merged without a word. Every run now states the input row
  count, the distinct stretches after merging and the share of window time absorbed, and says
  so at the top when that share reaches 10 percent. The counts are under `coverage` in
  `check_result.json`.
- The note about incident windows outside the range moves to the top when every incident window
  falls outside it.
- The section 8b flag-everything guard in `fdes/checks.py` was one sided, so a detector with
  recall at or above 0.95 was excluded whatever its F1, including a perfect detector. It is now
  two sided and shared by the archive path and `check`. No archived detector tripped the old
  rule, so archived values are unchanged.

### Known limits

- `check` scores time buckets, not incident episodes, so a detector that pages at minute 3 of a
  200 minute incident is charged for the remaining buckets.
- Prevalence, the floor and the alerted rate depend on the bucket size and time range, so lift
  figures at different buckets are not comparable. The sweep shows the effect without removing
  it, and it does not run in scores mode.
- The implausibly-long and lopsided thresholds are judgement calls. The report prints the
  underlying numbers.
- The scope check compares names for equality, so `checkout` and `checkout-api` read as no
  overlap. Normalise the names first.
- Postmortem windows start when impact was noticed, so an early-firing detector is charged false
  positives.
- The reported threshold is chosen in sample and describes this data only.
- Overlap is measured in buckets, so at a coarse bucket the absorbed share reads higher than the
  clock time the windows share.
- The degenerate-output guard measures spread relative to the mean, so a series like
  `1e9 +/- 100` (relative spread 2e-7, under the 1e-6 tolerance) is excluded although its
  ordering carries information. Centring the series works around it. A fix needs a
  specification-level decision, because it changes verdicts near the tolerance.

## [1.1.0] - 2026-08-31

The state of the package before the bring-your-own-data path. Published at
[10.5281/zenodo.22185619](https://doi.org/10.5281/zenodo.22185619). No changelog was kept before
this version.

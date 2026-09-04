# Changelog

## Unreleased

### Added
- `check`, a bring-your-own-data path. It evaluates a detector against your own
  monitoring output rather than the archived benchmark, and needs no download.
  Two input shapes, alert windows or per-timestamp scores, both as CSV, with
  incident windows as ground truth. `make check` runs it against the bundled
  examples. See the README section "Checking your own detector".
- `examples/`, sample CSVs including a deliberately useless detector, so the
  failure mode can be seen without exporting anything.

### Fixed
- **A run that could not be evaluated reported three passes.** When the ground truth is
  unusable, for example when every incident window falls outside the observation range, the
  prevalence is zero and so is the predict-all floor. An F1 of zero then sits exactly on the
  floor, so the lift guard, the flag-everything guard and the degenerate-output guard all
  printed `pass` and the floor row printed `F1 minus floor = +0.000`. A reader skimming saw
  three pass marks and a plus sign on a run holding no information, which is the exact
  failure this specification exists to criticise.

  Each check now reports one of three states rather than two. A check that cannot be
  evaluated says `not evaluable`, distinctly from passing and from failing, and an
  unevaluable run reports no passes at all. `check_result.json` carries the per-check
  `check_status` alongside the existing boolean `checks`.
- **`EXCLUDE` meant two opposite things.** It covered both "your detector is worthless" and
  "your ground truth is unusable". One says fix your detector, the other says fix your
  input. There is now a third verdict, `INSUFFICIENT`, with exit code 3. It fires when the
  input cannot support a verdict at all: prevalence zero, no incident window inside the
  range, an empty incident CSV, every bucket in one class, or every alert window falling
  outside the range. The reason is printed at the top of the report next to the verdict
  rather than under a heading further down. `PASS` stays 0 and `EXCLUDE` stays 2.

  **Behaviour change.** An alert CSV whose rows all fall outside the range used to report
  `EXCLUDE` as "the detector alerted on no bucket". Rows that all miss the range point at a
  wrong `--from` and `--to`, so that case is now `INSUFFICIENT`. An alert CSV with no rows
  at all is unchanged. That is a detector that never fired, it is a real result, and it
  still gets `EXCLUDE`.
- **Overlapping alert windows vanished silently.** Windows are unioned into one alerted
  mask, which is the right model for "was this bucket alerted", but the report gave no hint
  that anything had been merged. On a real Datadog Watchdog export 156 stories collapsed to
  95 distinct stretches and about a quarter of the alert time was absorbed, while the report
  said only `Input alerts: 156 rows`. Concurrent latency stories across different services
  overlap constantly, so this is not an edge case.

  The union is kept. Every run now states the input row count, the number of distinct
  stretches after merging, and the share of window time absorbed by overlap. When the
  absorbed share reaches 10 percent the report says so at the top as well. The counts are in
  `check_result.json` under `coverage`.
- The footer line about incident windows falling outside the range stayed below the verdict
  even when every incident window fell outside it. When that fact is the whole story it now
  appears at the top as the reason the run could not be evaluated.

  All four changes above are confined to the `check` path in `fdes/byod.py` and its exit
  codes. `fdes/checks.py`, `fdes/protocol.py` and `fdes/tables.py` are untouched, and
  `make smoke`, `make verify`, `make pilot` and `make verify-archive` reproduce the archived
  values to four decimals with the recorded verdicts byte identical.
- **Behaviour change against the v1.1.0 archive.** The section 8b flag-everything
  guard in `fdes/checks.py` was one sided. Any detector with recall at or above
  0.95 was excluded regardless of how far its F1 sat above the predict-all floor,
  so a perfect detector (F1 1.0, recall 1.0) was wrongly excluded. It is now two
  sided and shared by both the archive path and `check`.

  No published result changes. No detector in the archived study was good enough
  to trip the old rule, so it never fired. `make smoke`, `make verify`,
  `make pilot` and `make verify-archive` all reproduce the archived values to four
  decimals and the recorded verdicts are byte identical, including OCSVM, which
  stays excluded. Anyone re-running an archived comparison with this code will get
  the same numbers. Anyone evaluating a near-perfect detector will get a different
  and correct verdict.

### Known limits
- `check` scores time buckets, not incident episodes. A detector that pages
  correctly at minute 3 of a 200 minute incident is charged for the remaining
  buckets. Episode-level evaluation exists in the training code and is not yet
  wired into this path.
- Prevalence, and therefore the floor, depends on the bucket size and time range
  you choose. Two operators using different buckets cannot compare lift figures.
- Postmortem windows start when impact was noticed rather than when the signal
  moved, so an early-firing detector is charged false positives.
- The reported threshold is chosen in sample. Treat it as a description of this
  data, not as a threshold to deploy.
- Overlap is measured in buckets, so two windows that merely touch the same bucket count as
  overlapping. At a coarse bucket size the absorbed share reads higher than the clock time
  the windows actually share.

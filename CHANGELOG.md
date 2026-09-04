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

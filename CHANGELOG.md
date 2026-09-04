# Changelog

## [1.3.1] - 2026-09-04

### Fixed

- **Two notices contradicted each other on the same number.** Found on a real production
  export, not on a constructed case. The alert-volume counterweight added in 1.3.0 fired on
  a bare lift over 1.0, while the near-floor notice covers a band of `NEAR_FLOOR_BAND` (0.20)
  either side of 1.0. A lift between 1.0 and 1.2 therefore printed both, a few lines apart:
  "F1 is 1.04 times the predict-all floor. The detector is finding incidents" against "F1 /
  floor is 1.04, within 20 percent of 1.0, so this detector scores about what flagging every
  bucket would score". Scoring four percent better than flagging every bucket, while missing
  two thirds of the incident time, is not evidence that a detector is finding incidents. It
  was the 1.2.0 problem inverted rather than a fix for it.

  The counterweight is now suppressed inside the near-floor band. It reuses
  `NEAR_FLOOR_BAND` rather than introducing a second constant, so the two notices cannot
  overlap by construction. Above the band nothing changes. The tests sweep the lift through
  the whole band and assert the sweep actually reaches it, so they cannot go quiet if the
  construction drifts.

- **The documented install line printed a warning that looks like a corrupt download.**
  `git clone --depth 1 --branch v1.3.0` emitted `warning: refs/tags/v1.3.0 ... is not a
  commit!` before anything else. Nothing was wrong and it resolved to the right commit, but
  it was the first thing a new reader saw. It is git's shallow-clone handling of an
  annotated tag object. Release tags are lightweight from 1.3.1 on, which clones clean.

### Unchanged

No archived result moved. `make verify` and `make verify-archive` both return PASS, and
`fdes_checks.csv`, `table4_single_signal.csv`, `vus.csv` and `pilot_report.md` are byte
identical to the reference run. 195 tests pass.

## [1.3.0] - 2026-09-04

Two independent reviewers ran v1.2.0 against the exports that had produced its defects and
both came back with findings. Everything below came from that.

### Changed

- **The flag-everything guard is one continuous curve.** It fires when the alerted rate
  squared reaches `ALERT_RATE_PRODUCT` (2) times prevalence, which is the same as saying the
  alerted rate multiplied by its ratio to prevalence reaches 2. `ALERT_RATE_SATURATION`,
  `ALERT_RATE_RATIO_FLOOR` and `ALERT_RATE_RATIO_MULTIPLE` are gone. `ALERT_RATE_MULTIPLE`
  stays, and is now only the ratio worth printing, never a threshold.

  The two-path shape in 1.2.0 narrowed the flat regions rather than removing them. A
  reviewer proved through the command line that two survived: below a prevalence of 0.02 the
  0.20 floor decided alone, and between 0.05 and 0.125 the 0.50 floor did. At a prevalence
  of 0.002 an alerted rate of 0.1989 passed and 0.2009 excluded, both alerting roughly a
  hundred times more often than anything was wrong, with the ratio irrelevant to the
  outcome. The 0.05 to 0.125 stretch matters more, because that is ordinary prevalence
  rather than a corner. Flat regions also made bucket sweeps read as unstable for a reason
  that had nothing to do with the detector, because a rounding-level move in prevalence
  could carry a fixed threshold past the alerted rate.

  The curve passes through both anchors the threshold design had already chosen. At a
  prevalence of 0.02 the bar is 0.20 and at 0.125 it is 0.50, exactly where the old floors
  sat, so this is a smooth join of the existing design and not a new opinion about where the
  bar belongs. Everywhere else it interpolates, so the ratio always binds. The ratio the bar
  allows falls from 45 at a prevalence of 0.001 to 4 at 0.125, where the old shape allowed
  200 at the same low end.

  A perfect detector still cannot be caught, and that is now provable rather than tested. It
  alerts exactly as often as things are anomalous, so it fires only when prevalence squared
  reaches twice prevalence, meaning a prevalence of 2. The bar also stays stricter than the
  baseline it exists to catch: at full recall it fires below a precision of the square root
  of half the prevalence, which is above prevalence for every prevalence under 0.5.

  The curve is stricter than the two-path shape everywhere except at the two anchors, so
  verdicts move in one direction only. Nothing that excluded now passes. Applying it to the
  120 archived folds changes no verdict, so `make verify`, `make verify-archive`, `make
  smoke` and `make pilot` are byte identical. `check_result.json` gains `alert_rate_bar`,
  the rate the detector had to stay under, and `alert_rate_path` is now `"curve"` or null.

- **`--no-sweep` can no longer turn a failing run into a passing exit code.** Both reviewers
  found this independently. The flag held the exit code as well as the reported verdict, so
  a run that was UNSTABLE came back at the single-bucket exit code, and a continuous
  integration job reading only that code passed and never saw the paragraph explaining why
  the verdict was bucket-dependent. A suppressed UNSTABLE now exits 4. The flag still holds
  the reported verdict, which is what it is for. `check_result.json` carries
  `sweep_suppressed_unstable`, and `--help` now points at it.

- **An exclusion on alert volume says that is what it is.** This guard is the one most
  likely to fire on a real detector that finds incidents and simply costs too much to page
  on. A reviewer built the case: prevalence 0.02, alerted rate 0.20, recall 1.000, F1 at
  4.64 times the predict-all floor, excluded. The verdict section gave one sentence about
  alert volume and never mentioned the lift or the recall, so an operator reading it would
  conclude the detector does not work. When the guard fires while the detector has lift over
  the floor or high recall, the exclusion now names both, says it is excluded on alert
  volume and not on failing to detect, and hands the trade back as a judgement about the
  reader's own on-call load.

- **The exclusion sentence lost its nested clauses.** It had two "which is" clauses inside
  one sentence. It now names the alerted rate, the bar at that prevalence, and the ratio.

### Corrected

- **A claim of measurement that cannot be supported.** The 1.2.0 entry below described a
  detector alerting on 40 percent of a fourteen day timeline at a prevalence of 0.0397 as
  measured. Neither production export matches it. One covers 35.7 days at prevalences of
  0.0198 to 0.0339, the other a week at 0.043. The case is real as a construction, and it is
  built that way in the test suite at 57 anomalous buckets of 1440, but it should not have
  been written as a measurement. The 1.2.0 entry now carries a correction note in place, and
  the README no longer makes the claim at all.

- **The README known-limit paragraph** described floors that no longer exist. It now records
  what the two production exports actually measured, and that the bar sits below all of it.

### Fixed

- The exit-code change was written against the wrong nesting level on the first attempt and
  silently did nothing. The end-to-end test added with it caught that, which is the reason
  it is an end-to-end test and not a unit test.

## [1.2.0] - 2026-09-04

### Changed
- **The alerted-rate guard now has two ways in.** It was `rate >= 0.5 AND rate >= 4 x p`,
  which reads as two conditions and behaves as one. Four times 0.125 is 0.5, so the multiple
  is implied by the rate whenever prevalence is under 0.125, and real incident data is
  essentially never above that. The guard was really just "alerts more than half the time"
  and the ratio never bound on anything. ⚠️ Corrected in 1.3.0: the next sentence said this
  case was measured, and it cannot be matched to either production export, so read it as a
  constructed boundary case. At a prevalence of 0.0397, a detector
  alerting on 40 percent of the timeline is alerting 10.1 times as often as
  anything was wrong, and it passed at exit 0 with the best F1 in the table. A rate of 0.499
  passed and 0.500 excluded, a step function with nothing else looking.

  The old path is unchanged. A second path now fires at `rate >= 0.20 AND rate >= 10 x p`.
  The floor of 0.20 is what protects the case the 0.5 floor was protecting, that a
  rare-incident detector legitimately alerts many times more often than incidents occur. At
  a prevalence of 0.001, alerting on 1 percent of the time is ten times prevalence and a
  twentieth of the new floor, so it keeps its `PASS`. The multiple of 10 is the price of the
  lower floor, and at full recall it means fewer than one alert in ten lands on an incident.
  A perfect detector fails the multiple on both paths at every prevalence. The band between
  the two paths is open on purpose. `ALERT_RATE_RATIO_FLOOR` and `ALERT_RATE_RATIO_MULTIPLE`
  in `fdes/checks.py` set it, `check_result.json` records which path fired under
  `alert_rate_path`, and the exclusion reason names the right floor. Applying it to the 120
  archived folds changes no verdict, so `make verify`, `make verify-archive` and `make
  pilot` are byte identical.
- **`--no-sweep` no longer hides a verdict it suppressed.** It skipped the sweep, so a run
  that would have been `UNSTABLE` at exit 4 came back as the single-bucket verdict at its
  own exit code with nothing said. On one real export the same data at the same bucket gave
  `UNSTABLE` at exit 4 with the sweep and `PASS` at exit 0 without it. The sweep now always
  runs. The flag still holds the verdict and the exit code at the bucket you passed, and the
  report says at the top that it suppressed an `UNSTABLE` and what the exit code would have
  been. `check_result.json` carries `sweep_suppressed_unstable` and
  `bucket_sweep.applied`.
- **An empty scope intersection with one scope on one side is read as a naming difference.**
  A second real run carried 31 scopes from Datadog's `service` tag, which are application
  names, against exactly 1 from PagerDuty's `service` field, which was a catch-all routing
  destination. Both vendors call the field `service` and mean different things by it, so the
  intersection is empty by construction. The report used to call that a possible system
  mismatch and tell the reader to filter both exports to the scopes they share, which leaves
  an empty file. It now says the two files are using two naming schemes, does not offer the
  filter remedy, and points at taking the incident scope from the alert payload or the
  incident title. An empty intersection with several names on both sides keeps the mismatch
  reading and loses only the unusable remedy. A genuine partial overlap is untouched.
  `SCOPE_NAMESPACE_MAX_SCOPES` in `fdes/byod.py` sets the threshold, and `results.scope`
  gains `empty_intersection`, `namespace_mismatch`, `single_scope_side` and
  `single_scope_name`.

### Added
- The alerted rate over prevalence, printed next to the verdict whenever it reaches four
  times prevalence and neither path of the guard fired, with how many times more often the
  detector alerted than anything was wrong, which floor or ratio kept the guard quiet, and
  why that floor exists. The ratio also joins the check table. Across two independent real
  datasets, twelve bucket sizes and two ground-truth variants the ratio ran from 11.7 to
  14.7 while the guard never once fired, so the number that carried the information was the
  one nothing printed. `check_result.json` carries `alerted_rate_over_prevalence` and
  `alerted_rate_ratio_high`.
- A thin-recall notice next to the verdict when a `PASS` sits on recall under 0.5. There is
  deliberately no minimum recall in the procedure, and the README section "There is no
  minimum recall" says why. One real run gave four instantaneous alerts over four days at
  recall 0.071, F1 0.130 and lift 1.85, and passed at exit 0 having missed 93 percent of the
  incident time. `LOW_RECALL_NOTICE` in `fdes/byod.py` sets the band and
  `check_result.json` carries it as `low_recall`.
- A README section, "Where the incident windows came from", on circular ground truth, plus a
  line in every report's assumption list saying provenance was not checked. Building
  incident windows by clustering the detector's own alerts gives near-perfect recall and a
  high lift because every incident was defined by an alert, and nothing in the numbers shows
  it.
- A README known limit recording that both of the guard's absolute floors sit above where
  the real detectors measured so far actually landed, so the ratio is doing the informing
  and the floors are doing the excluding.
- A README section on getting alert data out of a real vendor, which is the hardest part
  of using `check` and was undocumented. It covers Datadog Watchdog specifically: there is
  no monitor behind Watchdog and it is not in the Datadog MCP surface, its output is Events
  and not Logs so searching Logs first gives a false negative, the Events API v2 query is
  `filter[query]=source:watchdog`, windows have to be rebuilt by grouping on the
  `story_key` tag and taking the minimum and maximum event timestamp because Watchdog emits
  one event per story update rather than a window, those reconstructed ends are the last
  update and not resolution so they are approximations invented at export time that this
  tool then treats as exact, pagination truncates silently (one run capped at 6000 events
  without saying so, and the tool only sees a CSV so it cannot tell the export was short),
  and `has_notification` has to be checked before anything is measured because an alert
  that never reached a human is a different object from a monitor that pages someone. It
  also warns generally that incident-tracker close times are ticket hygiene rather than
  impact, and that building incident windows from when alerting started and stopped is
  usually closer to the truth.
- `--scope-col` and `--incident-scope-col` for the optional service or scope column, plus
  recognition of `service`, `service_name`, `scope`, `entity` and `component` by name.
- `--no-sweep`, which turns off the bucket sweep and gives the single-bucket verdict and
  its exit code back.
- A fourth verdict, `UNSTABLE`, with exit code 4. It means the verdict changed across
  bucket sizes, so no single verdict is the result. It is alerts mode only.
- `check_result.json` gains `incident_concentration`, `bucket_sweep` and `scope` under
  `results`, and `scope_column` and `distinct_scopes` under each window file in `inputs`.
- `check`, a bring-your-own-data path. It evaluates a detector against your own
  monitoring output rather than the archived benchmark, and needs no download.
  Two input shapes, alert windows or per-timestamp scores, both as CSV, with
  incident windows as ground truth. `make check` runs it against the bundled
  examples. See the README section "Checking your own detector".
- `examples/`, sample CSVs including a deliberately useless detector, so the
  failure mode can be seen without exporting anything.
- A second reading of FDES section 8b on the alerted rate, `alert_rate_saturated()`
  in `fdes/checks.py`, applied by `check` as
  `alert_rate_far_above_prevalence`. It fires when the detector alerts on at
  least half the wall-clock time and at least four times as often as anything is
  anomalous. The report gains a row for it and `check_result.json` gains the
  check and its state.
- A near-the-floor notice next to the verdict when the lift lands within 20
  percent of 1.0 either way, with the lift, a plain statement that a result that
  close to the floor is not stable, and a suggestion to re-run at other bucket
  sizes. `check_result.json` carries it as `near_floor`.
- A point-events notice next to the verdict when a window file is read as
  instants because no end column was recognised. It names the columns the file
  did have and the flag that fixes it. `check_result.json` now records the full
  column list of every window file under `inputs`.

### Fixed
- A doubled full stop in the mixed-timezone assumption line.
- **Regression, zero-length windows are accepted again.** A row that starts and ends at
  the same second is an instantaneous event, not a broken row. A previous change refused
  the whole file, which rejected real vendor exports outright. A Datadog Watchdog story
  that emitted a single event has one timestamp, so start equals end, and 9 of 157 rows
  in one real export were like that. The only remedy offered was dropping the end column,
  which would have turned the other 148 real windows into point events too.

  These rows now mark the one bucket that contains them, which is what `mark_windows`
  always did and still documents. They are reported in the assumptions so they are not
  handled silently. A row that ends BEFORE it starts is unchanged and still refused,
  because that is impossible rather than instantaneous.

### Fixed
- **Two forgotten tickets destroyed the result and the tool said nothing.** On a real
  PagerDuty export of 23 incidents over 35.6 days, two rows had been sitting open for 13
  days and 12 days and accounted for 601 of the 620 total incident-hours. The remaining 21
  incidents held 19 hours between them. Prevalence came out at 0.383 instead of 0.020, a
  19-fold move, and the verdict turned over. Nobody was in a 13 day outage. Somebody forgot
  to close a ticket.

  The existing assumption text warned that tracker times are imprecise, but that warning is
  about minutes of slop at the window edges, not about one row spanning a third of the
  observation window. Every incident tracker has forgotten-open tickets, so this hits
  almost everyone who runs the tool.

  An incident row is now called implausibly long when it covers at least 10 percent of the
  observation range **and** runs at least 10 times the median incident in the same file.
  Both conditions are needed. Without the first, ordinary spread is condemned: where the
  median row is ten seconds, a five minute blip is 30 times the median and normal. Without
  the second, a real outage in a short window is condemned: four hours of a single day is
  17 percent of the range and there is nothing wrong with it. At least three in-range rows
  are needed, because a median of one or two rows is not a median.

  When it fires, the report says so next to the verdict. It names how many rows, how long
  they are, what share of the range and what multiple of the median that is, what share of
  all incident time they own, and what prevalence would have been without them. It says
  plainly that a small number of rows own most of the positive class and the result rests
  on them. Nothing is dropped, because which rows are real is the user's call. The rows are
  listed in `check_result.json` under `incident_concentration`.

  The general form is reported too. When the longest tenth of the incident rows owns half
  or more of all incident time, the report says the ground truth is lopsided and names the
  share, whether or not any single row looks broken.
  `LONG_INCIDENT_RANGE_SHARE`, `LONG_INCIDENT_MEDIAN_MULTIPLE` and `CONCENTRATION_NOTICE`
  in `fdes/byod.py` set the thresholds.
- **The bucket size chose the verdict.** On the same export the verdict was `EXCLUDE` at 1m
  and 5m and `PASS` at 15m, 30m, 1h and 2h, with the lift climbing monotonically through
  0.82, 0.90, 1.05, 1.21, 1.25 and 1.28. The detector never changed. The cause is
  mechanical: a coarse bucket lets one short alert cover an entire incident bucket for
  free, so recall rises with the bucket size while precision is barely charged for it. The
  operator picks the bucket, and by picking the bucket they pick the answer.

  The near-the-floor notice added earlier was not enough here. It says the margin is thin.
  It does not say whether the answer moves.

  Alerts mode now runs the whole check at 1m, 5m, 15m and 1h, plus whatever `--bucket` was
  passed, and reports the verdict at each. When they all agree the verdict stands and the
  report says the sweep was run and what it found, with the table under the checks. When
  they disagree the verdict is `UNSTABLE` and the report leads with the table, says which
  buckets gave which answer, and says that the single-bucket numbers below it are reported
  for the record and are not the answer. A monotonic lift is named as the signature of the
  mechanism rather than of a detector that works at one time scale. Only `PASS` and
  `EXCLUDE` rows are compared: a bucket that comes out `INSUFFICIENT` is listed and left
  out, because it says there was nothing to measure there and not that the answer flipped.

  The sweep is alerts mode only. In scores mode the operating point moves with the bucket
  as well as the bucket boundaries, so a sweep there would change two things at once. The
  near-the-floor notice now points at the sweep result instead of asking the user to re-run
  the thing themselves. `SWEEP_BUCKETS` in `fdes/byod.py` sets the ladder.
- **Alerts and incidents could describe different systems and nothing noticed.** In the
  same run Watchdog covered 34 services while all 23 PagerDuty incidents came from a single
  service, so most of the 156 alerts could not possibly have corresponded to any incident
  in the file, and every one of them was scored as a false positive. The CSVs carry no
  notion of scope, so precision, F1 and the verdict were all measuring the mismatch rather
  than the detector.

  Both window CSVs can now carry an optional service or scope column. When both have one,
  the report gives the distinct scope count on each side, how many they share, and what
  share of the alert rows fall on scopes that appear in no incident. When at least half of
  the alert rows fall on unmatched scopes, the report says next to the verdict that the two
  files may be describing different systems and that the result is unreliable.

  Scope is reported and never applied. No row is filtered by it and no number moves because
  of it, so the same two files give the same verdict with the column and without it. When
  the columns are absent the run behaves exactly as before, and the report says in the
  assumption list that scope was not checked and what that risks. Scores mode always says
  this, because a score series has no scope column to compare with.
  `SCOPE_OVERLAP_POOR` in `fdes/byod.py` sets the threshold.

  **No published result changes for any of the three.** All of it is on the `check` path.
  `tables/fdes_checks.csv`, `tables/table4_single_signal.csv` and `tables/vus.csv` are still
  byte identical against `runs/2026-08-29-smoke-m4pro/`, and `make smoke`, `make verify`,
  `make verify-archive` and `make pilot` all pass unchanged.
- **A flag-everything detector reported PASS.** On a real Splunk and PagerDuty export the
  tool returned `PASS` for a detector that alerted on 94.5 percent of the week. Prevalence
  0.043, recall 1.00, precision 0.045, F1 0.087 against a predict-all floor of 0.082.

  Section 8b excludes a detector whose F1 is within 5 percent of the floor while recall is
  at or above 0.95. Recall was 1.0, but F1 sat 5.6 percent above the floor, which clears a
  5 percent margin by six tenths of a percentage point, so the guard did not fire and
  nothing else excluded it. At a 15 minute bucket the same detector alerted 95.4 percent of
  the time and was correctly excluded, so the verdict turned on the bucket size. This is
  the exact detector the procedure exists to catch.

  The fix reads section 8b a second time, on the alerted rate against prevalence rather
  than on F1 against the floor. A detector that works alerts about as often as things are
  anomalous. A detector that flags everything alerts far more often than anything is wrong.
  The guard fires when the alerted rate is at or above 0.5, so the detector spends the
  majority of the wall-clock time alerting, and at or above four times prevalence. Both
  conditions are needed. Without the first a rare-incident detector alerting on 1 percent of
  the time at a prevalence of 0.001 would be condemned for working. Without the second a
  perfect detector on data that is 60 percent anomalous would be condemned for the shape of
  its data. The alerted rate over prevalence is recall over precision, so the multiple of
  four says that at full recall no more than one alert in four lands on an incident.

  The earlier one-sided form of section 8b excluded any detector with recall at or above
  0.95 whatever its F1, which wrongly excluded a perfect detector. Making it two sided fixed
  that and opened this hole. The new guard closes the hole without reopening that defect,
  because a perfect detector has an alerted rate equal to prevalence and so cannot satisfy
  the second condition at any prevalence. Both ends are tested.

  **No published result changes.** The guard is applied on the `check` path only.
  `tables/fdes_checks.csv` has no alerted-rate column and is compared byte for byte against
  the recorded run, so adding one there would move archived output. Applying the same rule
  to the 120 archived folds fires on none of them, so the two paths agree on the published
  data. The archived alerted rate is available as `predicted_anomalies / total_samples` in
  `expected/model_results_per_fold.csv` for anyone who wants to check that.
- **The verdict moved with the bucket size and the report did not say so.** On the same
  export the lift was 1.17, 1.06, 1.05 and 1.02 at four bucket sizes and the verdict flipped
  between `PASS` and `EXCLUDE`, while the numbers never left the neighbourhood of 1.0. The
  bucket size is a parameter the user picks arbitrarily.

  A lift within 20 percent of 1.0 either way now gets its own line next to the verdict. It
  gives the lift, says the detector scores about what flagging every bucket would score,
  says plainly that a result this close to the floor is not stable and can move with the
  bucket, and names other bucket sizes to try. `NEAR_FLOOR_BAND` in `fdes/byod.py` sets the
  band.
- **Guessing the schema flipped verdicts quietly.** A realistic Splunk export carries
  `_time`, `_indextime`, `earliest` and `latest`. The tool recognised `_time` as a start,
  recognised nothing as an end, and read every row as a point event covering one bucket.
  On real data that turned a `PASS` into an `EXCLUDE` at three bucket sizes, and it was
  disclosed only as one line in the assumption list below the verdict.

  Reading every row as an instant is an interpretation of the input, so it now appears next
  to the verdict. The notice names the file, the start column it used, the fact that no end
  column was recognised, and the full list of columns the file did have, so `latest` sitting
  unused is visible. It names `--end-col`, or `--incident-end-col` for the incidents file,
  as the fix. Those flags already existed.
- **An empty file was described by its rows instead of its header.** A zero-row CSV with a
  valid `end` column in its header was reported as having a start column but no end column,
  because the point-event decision was taken when the file had no rows. The schema is now
  read off the header. A header with an end column means windows, however many rows follow.
- **A zero-length window passed in silence.** A window that ends before it starts is refused
  with a message naming the columns and the row count. A window that ends the same second it
  starts covers no time in the same way and was marked onto one bucket without a word. It is
  now refused the same way, with the same shape of message, and the message says to export a
  start column only if the rows really are instantaneous events. A file with no end column is
  unaffected, because it has no end to disagree with its start.
- **Exit code 2 read as a crash.** `EXCLUDE` exits 2 and the first person to wire this into
  continuous integration read that as the tool failing. The exit codes are now documented in
  the top-level `--help`, in `check --help` and `pilot --help`, and in the README table: 0
  for `PASS`, 2 for `EXCLUDE`, 3 for `INSUFFICIENT` and 1 for a real failure. No verdict
  shares a code with an error, so 1 always means the command broke and never means a
  detector was rejected. `check` now exits through one `fail()` helper for every input
  problem, and a test pins the codes apart.
- **A single-class fold reported a silent PASS on the pilot path.** `checks_from_scores()`
  sets AUC-ROC and PR-AUC to nan when the evaluated windows carry one class, because no
  rank metric is defined there. `check_row()` then evaluated `nan <= 0.5`, which is False,
  so the section 8a exclusion never fired and the run returned `PASS`. Precision, recall
  and F1 carry no information on one class either, so the report printed a pass mark and a
  measured-looking AUC-ROC on a run holding nothing. This is the same defect that was fixed
  on the `check` path, and it was worse here because this is the published methodology.

  `check_row()` now reports each check in one of three states, the same as `check` does. A
  fold that carries one class is `INSUFFICIENT`, every check on it says `not evaluable`,
  and no rank metric is printed. An exclusion that did fire still outranks a check that
  could not be applied, so a genuine `EXCLUDE` is not softened into `INSUFFICIENT`.
  `pilot_result.json` carries the per-check `check_status` and the reason. `make pilot` now
  exits 3 on `INSUFFICIENT`, matching `make check`.

  **No published result changes.** Every archived fold carries both classes, so the new
  states never fire on them. `make smoke`, `make verify`, `make verify-archive` and
  `make pilot` reproduce every archived value to four decimals, and `fdes_checks.csv`,
  `table4_single_signal.csv` and `vus.csv` are byte identical to the recorded run under
  `runs/2026-08-29-smoke-m4pro/`. The columns of `fdes_checks.csv` are unchanged, and a
  test pins them.
- **The alerts column overrides were applied to the incidents file as well.** `--start-col`
  and `--end-col` were passed to both CSVs, so an operator with a Watchdog export using
  `triggered_at` and `resolved_at` alongside an incident tracker using `start` and `end`
  could not run the tool at all. The override that found one file's columns broke the
  other's.

  There are now `--incident-start-col` and `--incident-end-col` for the incidents CSV.
  `--start-col` and `--end-col` keep their meaning, the alerts CSV, and the incident
  overrides fall back to them when not given, so a run that passes only those two behaves
  as before. In scores mode the incidents CSV is the only window file and the same fallback
  applies.
- **The recorded pilot report under `runs/` was stale.** It predated the VUS row, so it no
  longer matched what `make pilot` prints. That directory is cited as evidence, so it has
  been regenerated. The VUS row is the only line that changed. Every other number, the
  verdict included, is identical. A test now compares the recorded report's rows against
  what the renderer writes, so it cannot drift again unnoticed.
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
  codes. `fdes/tables.py` is untouched, the three-state helper they introduced now lives in
  `fdes/checks.py` so that the pilot path shares one definition of it, and `make smoke`,
  `make verify`, `make pilot` and `make verify-archive` reproduce the archived values to
  four decimals with the recorded verdicts byte identical.
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
  The alerted rate moves with the bucket for the same reason, so the alerted-rate
  guard is read on the bucket you picked. Alerts mode now sweeps four bucket sizes
  and refuses to hand back one verdict when they disagree, which shows the effect
  rather than removing it. There is still no principled way to pick the bucket, and
  the sweep does not run in scores mode.
- The implausibly-long and lopsided thresholds are judgement calls, not results.
  Ten percent of the range and ten times the median catch the forgotten tickets we
  have seen and leave real long outages alone, and a file can sit on either side of
  either line for honest reasons. The report gives the underlying numbers so the
  call can be made by hand.
- The scope check compares scope strings for equality. Two exports naming the same
  service differently (`checkout` against `checkout-api`, or a Datadog tag against
  a PagerDuty service name) read as no overlap at all. Normalise the names in the
  CSVs before relying on the share it reports.
- Postmortem windows start when impact was noticed rather than when the signal
  moved, so an early-firing detector is charged false positives.
- The reported threshold is chosen in sample. Treat it as a description of this
  data, not as a threshold to deploy.
- Overlap is measured in buckets, so two windows that merely touch the same bucket count as
  overlapping. At a coarse bucket size the absorbed share reads higher than the clock time
  the windows actually share.
- The near-constant test in the degenerate output guard measures the spread of the score
  column against its mean, so a large constant offset reads as a constant column. A series
  of `1e9 +/- 100` has a relative spread of 2e-7, under the 1e-6 tolerance, and is excluded
  even though its ordering carries real information that AUC-ROC and PR-AUC would use.
  Fixing it means choosing a noise floor that does not depend on the offset, which changes
  the verdict of any run whose relative spread sits between the old and new tolerances, so
  it is left for a spec-level decision rather than settled here. The guard refuses rather
  than passes, and the report prints `score_spread` and `distinct_scores` so the call can
  be checked. Centring the series works around it.

## [1.1.0] - 2026-08-31

The state of the package before the bring-your-own-data path existed. Published at
[10.5281/zenodo.22185619](https://doi.org/10.5281/zenodo.22185619). No changelog was kept
before this point, so the 1.2.0 entry above is the first one.

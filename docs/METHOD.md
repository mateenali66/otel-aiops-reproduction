# How the checks work

The README explains how to run the tool. This file explains what each check in `make check`
measures, why it is built the way it is, and what it cannot tell you. `CHANGELOG.md` lists
the changes by version.

## Where the incident windows came from

The check compares one set of windows against another. It cannot tell whether the two came
from the same place. If your incident windows were built from the alerts you are scoring, or
from anything downstream of the detector under test, the result is circular and means
nothing. It will usually look excellent.

This happens easily. Incident windows derived by clustering the detector's own alerts give
near-perfect recall and a high lift, because every incident was defined by an alert. Tickets
opened by the monitoring system under test, or a tracker that creates incidents from those
alerts, do the same thing one step removed.

Ground truth has to come from somewhere the detector cannot reach. Human-declared incidents,
customer-reported outages, postmortems and a monitoring stack with no shared inputs all
qualify. If you
must derive windows from alerts, split by provenance first and score the detector's alerts
only against windows built from sources it does not feed.

State where your windows came from when you report a result. Without that, a verdict cannot
be interpreted either way. The tool cannot check provenance, so every report lists it as an
assumption.

## Getting alert data out of a real vendor

The export decides the result more than any check does.

Datadog Watchdog needs particular care.

- Watchdog is not a monitor, so tools that list monitors report no Watchdog data. Its output
  is Events, not Logs, so searching Logs returns nothing.
- Use the Events API v2 with `filter[query]=source:watchdog` to get the raw stream.
- Watchdog emits one event per story update. To get windows, group the events on the
  `story_key` tag and take the minimum and maximum event timestamp in each group.
- The maximum is the last update Watchdog sent, which is usually before the story ended.
  These end times are reconstructed at export, and the tool treats them as exact, so false
  negatives near the end of a window may come from the reconstruction.
- Pagination can truncate without warning, and a short export looks the same as a quiet
  week. Page until the cursor is exhausted and count the rows before you trust the file.
- Check `has_notification`. A story that never paged anyone is a different thing from a
  monitor that woke someone up. Decide which you are evaluating and filter to it.

For incident trackers in general, a close time in PagerDuty, Jira or ServiceNow records when
someone clicked resolve, not when impact stopped. Tickets get closed at the end of a shift,
on Monday morning, or never, so windows built from close times can be much longer than the
incident. Where you can, build incident windows from when alerting started and stopped,
using a different signal from the one under test or the on-call paging record. When you
cannot, read the next section.

## Incident rows nobody closed

A few tickets left open can dominate the ground truth. In one export of 23 incidents over
35.6 days, two tickets open for 13 and 12 days held 601 of the 620 incident-hours. Prevalence
came out at 0.383 instead of 0.020 and the verdict changed.

An incident row is flagged as implausibly long when both of these hold.

1. It covers at least 10 percent of the observation range.
2. It runs at least 10 times the median incident in the same file.

Each condition protects a case the other gets wrong. Without the range condition, ordinary
spread is flagged. Where the median row is ten seconds, a five-minute blip is 30 times the median and
normal. Without the median condition, a real outage in a short observation window is flagged. Four
hours of a single day is 17 percent of the range. The guard needs at least three rows in
range to compute a median.

When it fires, the report says so next to the verdict. It gives the number of rows, their
length, their share of the observation range, their multiple of the median, their share of
all incident time, and what prevalence would be without them.

No row is dropped. Deciding which rows are real is up to you. The rows are listed in
`check_result.json` under `incident_concentration` with their start, end, length and shares,
so you can look them up in the tracker.

The report also flags a lopsided file whether or not any single row looks broken. When the
longest tenth of the incident rows owns half or more of all incident time, the report names
the share, because correcting one or two end times would move the result.
`LONG_INCIDENT_RANGE_SHARE`, `LONG_INCIDENT_MEDIAN_MULTIPLE` and `CONCENTRATION_NOTICE` in
`fdes/byod.py` set the thresholds.

## The bucket size chooses the verdict

The same detector can get different verdicts at different bucket sizes. In one example the
verdict was `EXCLUDE` at 1m and 5m and `PASS` at 15m, 30m, 1h and 2h, with the lift rising
at every step (0.82, 0.90, 1.05, 1.21, 1.25, 1.28). A coarse bucket lets one short alert
cover a whole incident bucket, so recall rises with the bucket size while precision barely
falls.

Alerts mode therefore runs the check at 1m, 5m, 15m and 1h, plus the `--bucket` you passed,
and reports the verdict at each.

- When every bucket agrees, the verdict stands and the sweep table appears under the checks.
- When they disagree, the verdict is `UNSTABLE`, exit code 4. The report leads with the
  table of verdicts and says that the single-bucket numbers below it are for the record.
  When the lift rises at every step, it names the coarse-bucket effect above as the likely
  cause.

`UNSTABLE` means this input cannot settle the question. Pick the bucket size from the time
scale your responders work at and be able to say why, or treat the result as undecided. Only
`PASS` and `EXCLUDE` rows are compared. A bucket size that comes out `INSUFFICIENT` is listed
and left out of the comparison.

`--no-sweep` holds the reported verdict at the bucket you passed, but a suppressed `UNSTABLE`
still exits 4, so a flag that changes what is printed cannot also hide a failure from a
script. `sweep_suppressed_unstable` in `check_result.json` tells the two apart.
`SWEEP_BUCKETS` in `fdes/byod.py` sets the ladder. The sweep runs in alerts mode only,
because in scores mode the operating point moves with the bucket as well as the bucket
boundaries.

## Scope, when the two files describe different systems

If the alert export covers 34 services and every incident comes from one service, most
alerts cannot match any incident and all of them are scored as false positives. A CSV
carries no notion of scope, so precision, F1 and the verdict would measure the mismatch
rather than the detector.

Both window CSVs can carry an optional service or scope column. `service`, `service_name`,
`scope`, `entity` and `component` are recognised by name, and `--scope-col` and
`--incident-scope-col` name any other column. `--incident-scope-col` falls back to
`--scope-col`.

```bash
python bin/reproduce.py check \
  --alerts watchdog_export.csv --start-col triggered_at --end-col resolved_at \
  --scope-col service \
  --incidents pagerduty.csv --incident-scope-col service_name \
  --bucket 5m --from 2026-03-01T00:00:00Z --to 2026-04-05T00:00:00Z
```

When both files have one, the report gives the number of distinct scopes on each side, how
many they share, and the share of alert rows on scopes that appear in no incident. When half
or more of the alert rows are on unmatched scopes, the report says next to the verdict that
the files may describe different systems.

Scope strings are compared for equality, so `checkout` and `checkout-api` count as no
overlap. Normalise the names in the CSVs first.

An empty intersection is often a naming difference. Datadog's `service` tag names the
application a signal came from. PagerDuty's `service` field names where the page was sent,
and many teams route every page to one catch-all service. Both vendors call the field
`service`, so the intersection is empty by construction and the unmatched share reads 100
percent whatever the detector did. When the intersection is empty and one side has exactly
one distinct scope, the report says the files use two naming schemes. It does not suggest
filtering both files to the shared scopes, which would leave them empty, and it suggests
taking the incident scope from the alert payload the incident was created from or from the
incident title.

The threshold is one distinct scope, because one value cannot be a partial list of
application names. Two or three names against thirty may be the same situation, but two
exports can also cover two small estates that do not overlap, so those keep the mismatch
reading without the filter suggestion. A partial overlap, where at least one name appears on
both sides, gets the original wording and the filter suggestion. `SCOPE_NAMESPACE_MAX_SCOPES`
in `fdes/byod.py` sets the threshold, and `check_result.json` carries `empty_intersection`,
`namespace_mismatch`, `single_scope_side` and `single_scope_name` under `results.scope`.

Scope is reported, never applied. No row is filtered and no number moves, so the same two
files give the same verdict with the column and without it.

When the columns are absent, the assumption list says scope was not checked and that alerts
on services with no incident in the file are counted as false positives. Scores mode always
says this, because a score series has one row per timestamp and no scope column.

## The alerted rate against prevalence

The F1 form of section 8b asks whether F1 sits within 5 percent of the predict-all floor
with recall at or above 0.95. Both numbers come from the same prevalence, so a detector that
alerts almost all the time can miss the margin by a rounding-level distance. For example, at
prevalence 0.043, recall 1.00, precision 0.045 and an alerted rate of 0.945, F1 is 0.087
against a floor of 0.082. That is 5.6 percent above the floor, just outside the margin, so
section 8b does not fire although the detector alerted on 94.5 percent of the week.

A working detector alerts about as often as things are anomalous, so its alerted rate sits
near prevalence. A detector that flags everything alerts far more often. The guard fires when
the alerted rate squared reaches twice prevalence. Since the alerted rate divided by
prevalence equals recall divided by precision, this is the same as the alerted rate times
its ratio to prevalence reaching 2. A detector may alert often, or alert far more often than
incidents occur, but not both. `ALERT_RATE_PRODUCT` in `fdes/checks.py` sets the 2, and
`alert_rate_bar` solves the rule for the rate so the report can print the bar.

The rule is a curve because a threshold that is constant in prevalence has flat regions
where the constant decides alone and the ratio never binds. With an absolute floor of 0.20,
at prevalence 0.002 a detector alerting on 19.89 percent of the timeline passes and one at
20.09 percent is excluded, although both alert about a hundred times more often than anything
is wrong. Flat regions also make a bucket sweep look unstable, because a small move in
prevalence can carry the threshold past a fixed alerted rate. The curve passes through 0.20
at prevalence 0.02 and 0.50 at 0.125 and moves with prevalence everywhere.

| prevalence | the bar | ratio it allows |
|---|---|---|
| 0.001 | 0.045 | 45 |
| 0.010 | 0.141 | 14 |
| 0.020 | 0.200 | 10 |
| 0.050 | 0.316 | 6.3 |
| 0.125 | 0.500 | 4 |
| 0.300 | 0.775 | 2.6 |

A perfect detector cannot trigger the guard. Its alerted rate equals prevalence p, so it
would need p squared to reach 2p, which means p of 2. Rare incidents are still covered: at a
prevalence of 0.001 the bar is 0.045, so a detector alerting on 1 percent of the time with
perfect recall, ten times prevalence, passes.

At full recall the guard fires below a precision of the square root of half the prevalence.
For every prevalence under 0.5 that is above prevalence itself, the precision that flagging
everything achieves.

### Bucket occupancy

The alerted rate is measured in buckets, and a window shorter than the bucket claims the
whole bucket. At a coarse bucket a detector that alerts briefly and often looks like one that
alerts continuously. Take a detector that catches six one-hour incidents in full and raises
six one-minute false alarms a day. It is in an alerting state 1.25 percent of the time
against a prevalence of 0.83 percent, so it alerts 1.5 times as often as anything is wrong.
At an hourly bucket every one-minute alarm claims a whole bucket and the alerted rate reads
0.25.

Before the guard excludes, the same rule is applied to the raw alert and incident durations.
If it does not fire there, the exclusion comes from the bucket size and is not applied. The
report says so, gives the duty cycle and the true ratio, suggests a bucket close to your
shortest alert, and gives the alert count and the precision. This applies in alerts mode
only, since scores mode has no durations.

`SUPPRESSION_MAX_LEVERAGE` limits how much of the alerted rate may be blamed on the bucket.
The quantity it caps is roughly the bucket length divided by the window length, so it only
asks whether the windows are very short relative to the bucket. It stops suppression on degenerate
exports and is not an independent check.

### When it fires on a detector that works

The guard can exclude a detector that finds incidents but costs too much to page on. At
prevalence 0.02, a detector alerting on 20 percent of the timeline with recall 1.000 scores
4.64 times the predict-all floor and is excluded. When the guard fires while the detector
has lift over the floor or high recall, the report says it was excluded on alert volume, not
for failing to detect, names the lift and the recall, and leaves the trade-off to you.

The guard applies in both modes. In scores mode it applies at whatever operating point the
threshold produces, so a score that ranks buckets well cannot reach `PASS` while its operating
point flags most of the timeline.

### Alert volume

The guard measures how much of the timeline a detector occupies, not how often it pages. A
detector that fires constantly in short bursts occupies little time, so it can clear every
check while paging more often than anyone would read. The report therefore gives the alerts
per incident, the alerts per day, the window count and the incident stretch count, and does
not make the judgement for you.

An alert window is not a page, because real stacks deduplicate and group alerts and the
export does not say which happened. The precision in the check table is measured per bucket,
so it is not the fraction of pages worth reading.

`ALERTS_PER_INCIDENT_LIMIT` is 50. Known reference points are one detector at 31 worth
keeping, two at 176 and 355 that are not, and one production stack at 49.47. A run within 10
percent of the limit says so. The denominator counts merged incident stretches rather than
rows, because the same two outages written as forty rows instead of two would otherwise move
the ratio from 355 to 17.75.

This check is not available in scores mode, because a score series has no discrete alert
windows to count. A scores-mode `PASS` therefore rests on fewer checks than an alerts-mode
one, in this respect as well as in section 8a.

The constant 2 has been checked against only a few real exports. The report prints the ratio
to prevalence next to the verdict whenever it reaches four times prevalence and the guard did
not fire.

## When prevalence is above a half

The guard's bar is the square root of twice prevalence. Above a prevalence of 0.5 the bar
exceeds 1.0, and an alerted rate cannot exceed 1.0, so the guard cannot fire. At a
prevalence of 0.853 the bar is 1.306, and the check table says `not reachable at this
prevalence`.

Nothing else can fail there either. Section 8b needs recall at or above 0.95, alerts mode
has no rank metric for section 8a, and the predict-all floor reaches 0.92, so F1 barely
separates a working detector from one that alerts on everything. A detector alerting on 88
percent of the timeline would pass with no check firing.

Such a run is `INSUFFICIENT` when the guard is unreachable and the detector also sits inside
the near-floor band. Prevalence alone is not enough: a perfect detector at a prevalence of
0.6 scores a third above the floor, which is a real measurement, and keeps its `PASS`. An
`EXCLUDE` still stands either way.

A prevalence above a half also gets a notice at the top of the report. It means most of the
observation window was inside an incident, which can be true on a range chosen around a long
outage but more often means the incident file records ticket lifetime rather than incident
time.

## Duplicate alert rows, and why the tool counts them anyway

An export that sends each notification to several recipients repeats each alert, with a
distinct row identifier and the same or nearly the same timestamp. That multiplies the
alerts per incident and makes the overlap notice describe a distribution list as if it were
the detector's behaviour. The alerted rate is not affected, because occupancy is a union and
duplicate windows claim buckets that are already claimed.

Exact duplicates are easy to match, but real delivery fan-out produces copies a second or two
apart. The check therefore also compares the row count with the number of separate stretches
the rows cover, since repeated deliveries of one alert collapse into one stretch however they
are jittered.

That second signal cannot tell fan-out from a bursty detector. Three real firings a minute
apart, each five minutes long, have the same shape as one alert delivered three times. The
report says so, and the finding is a notice, not a verdict.

## There is no minimum recall

Nothing in this procedure sets a floor on recall, and lift alone can carry a `PASS`. For
example, four instantaneous alerts over four days gave recall 0.071, F1 0.130, lift 1.85 and
`PASS` at exit 0, while missing 93 percent of the incident time.

A high-precision detector that fires rarely and is usually right is worth keeping. Bucket
recall also depends on the export format. Instantaneous alerts cover one bucket each while an
incident covers many, so a recall floor would mostly measure the export.

When the verdict is `PASS` and recall is under 0.5, the report says next to the verdict that
the detector missed more incident time than it caught, and gives the precision and the floor
that carried the verdict. `LOW_RECALL_NOTICE` in `fdes/byod.py` sets the band, and
`check_result.json` carries it as `low_recall`.

## When the lift sits near the floor

The lift is F1 divided by the predict-all floor. A lift near 1.0 means the detector scores
about what flagging every bucket would score, so the verdict turns on a rounding-level
difference. In one example the lift was 1.17, 1.06, 1.05 and 1.02 at four bucket sizes and
the verdict still flipped between `PASS` and `EXCLUDE`.

When the lift is within 20 percent of 1.0 either way, the report says so next to the verdict
and names the lift. In alerts mode it points at the sweep result, which shows whether the
answer actually moves. `NEAR_FLOOR_BAND` in `fdes/byod.py` sets the band, and
`check_result.json` carries the flag as `near_floor`.

## Overlapping alert windows

Windows are unioned into one alerted mask, because two concurrent alerts on the same bucket
are still one alerted bucket. Concurrent alerts are common, for example latency stories on
several services at once, so a row count can overstate alert time. In one Watchdog export,
156 stories merged into 95 distinct stretches and about a quarter of the alert time sat under
another story.

Every run states the input row count, the number of distinct stretches after merging, and
how many buckets were absorbed. When the absorbed share reaches 10 percent the report also
says so at the top. The counts are in `check_result.json` under `coverage`.

## The time range, and why it is not guessed

Alerts and incidents say when something happened, not when you were watching. `check`
therefore does not infer the evaluation range from them. It asks for `--from` and `--to` and
prints the span of your data so you can copy it.

`--infer-range` uses that span anyway. That removes every quiet period outside the events,
which inflates prevalence and flatters precision. It is meant for a quick look, and the
report states that it was used.

## What it computes

| Check | Spec section | Alerts mode | Scores mode |
|---|---|---|---|
| Prevalence p from the ground-truth buckets | 2 | yes | yes |
| Precision, recall, F1 at the operating point | 6 | yes | yes |
| Predict-all baseline F1 = 2p/(1+p), and F1 minus it | 2, 7 | yes | yes |
| Lift over the predict-all baseline | 7 | yes | yes |
| Flag-everything guard on F1 and recall | 8b | yes | yes |
| Flag-everything guard on the alerted rate against prevalence | 8b | yes | yes |
| The alerted rate over prevalence, reported when it is high and the guard did not fire | input quality | yes | yes |
| A notice when a `PASS` rests on recall under 0.5 | input quality | yes | yes |
| Degenerate output guard (alerts on all, alerts on none, one near-constant score) | 8 | yes | yes |
| Implausibly long and lopsided incident rows | input quality | yes | yes |
| Verdict at 1m, 5m, 15m and 1h, and whether they agree | input quality | yes, and `--no-sweep` still reports it | no |
| Scope overlap between the two files, and whether an empty one is a naming difference | input quality | yes, when both files carry a scope column | no |
| AUC-ROC against its 0.5 reference | 6, 7, 8a | no | yes |
| PR-AUC against its p reference | 6, 7 | no | yes |
| VUS-PR and VUS-ROC | 6 | no | yes, when every bucket in the range holds a score sample |
| Degenerate rule from the article's Table 12 | 8 | no | yes |

The near-constant test in the degenerate output guard measures the spread of the score
column against its mean, so a large constant offset reads as a constant column. A series of
`1e9 +/- 100` has a relative spread of 2e-7, under the 1e-6 tolerance, and is excluded
although its ordering carries information. The guard errs towards refusing, and the report
prints `score_spread` and `distinct_scores` so you can check the call. Centre the series or
subtract the offset to avoid it. `CONSTANT_SPREAD_RATIO` in `fdes/byod.py` sets the tolerance.

## What alerts mode refuses to compute, and why

A threshold-independent rank metric cannot be computed from binary alerts. A rank metric
sweeps the threshold and measures how well the scores order the buckets. An alert window is
a decision with the threshold already applied, so there is no ordering left to sweep.

An AUC function given a 0/1 vector does return a number, but it is a rescaling of the
balanced accuracy at the one operating point you already have, a different quantity from a
score-based AUC-ROC. Alerts mode does not print it. The report's "What this run could not
compute" section names AUC-ROC, PR-AUC, VUS-PR and VUS-ROC and gives the reason.

Section 8a excludes a detector whose threshold-independent score sits at or below its random
reference. Without a rank metric it cannot fire, and the report says so. A `PASS` in alerts
mode rests on fewer checks than a `PASS` in scores mode and is not evidence that section 8a
would have been passed. To get the rest, export the underlying anomaly score or deviation
series and use scores mode.

Scores mode refuses the same metrics when the score column holds two or fewer distinct
values, because a 0/1 column is alerts in another form.

## The threshold in scores mode

FDES section 5 step 2 requires the threshold to be picked on a validation split disjoint
from the test set, which one CSV cannot provide.

- Pass `--threshold` with the value your monitor uses to get your real operating point.
- Without it, the threshold is tuned in sample to maximise F1. That is an optimistic upper
  bound, and the report says so every time.

`--aggregate max` (the default) or `--aggregate mean` reduces several score samples inside
one bucket. Buckets with no score sample are dropped from the evaluation, not treated as
low-scoring, and the report counts them. Dropping them leaves gaps in the time axis, so VUS,
which is range based, is skipped whenever any bucket is empty. AUC-ROC and PR-AUC ignore
order and are reported either way.

## The constants, and where to change them

Every threshold in these checks is a named constant. If your operation has a different
tolerance, change the constant rather than working around the verdict. The values below are
those in v1.5.0. The source is authoritative.

| Constant | Value | File | What it sets |
|---|---|---|---|
| `ALERTS_PER_INCIDENT_LIMIT` | `50.0` | `byod.py` | alerts per merged incident stretch above which alert volume is reported as too high |
| `ALERT_RATE_MULTIPLE` | `4.0` | `checks.py` | the ratio to prevalence above which the ratio is printed even when the guard did not fire |
| `ALERT_RATE_PRODUCT` | `2.0` | `checks.py` | the alerted-rate guard, which fires when the alerted rate squared reaches this times prevalence |
| `CONCENTRATION_MIN_ROWS` | `5` | `byod.py` | minimum incident rows before the lopsided-file notice applies |
| `CONCENTRATION_NOTICE` | `0.50` | `byod.py` | share of incident time owned by the longest rows that triggers the lopsided-file notice |
| `CONCENTRATION_TOP_FRACTION` | `0.10` | `byod.py` | the fraction of rows treated as the longest for that notice |
| `CONSTANT_SPREAD_RATIO` | `1e-6` | `byod.py` | relative spread at or below which a score column counts as near-constant |
| `DEGENERATE_AUC` | `0.55` | `checks.py` | AUC bound in the article's Table 12 degenerate rule |
| `DEGENERATE_F1_RATIO` | `0.95` | `checks.py` | F1-to-floor ratio in the article's Table 12 degenerate rule |
| `DOMINANT_ALERT_MIN_ROWS` | `5` | `byod.py` | minimum alert rows before a dominant window is named |
| `DOMINANT_ALERT_SHARE` | `0.30` | `byod.py` | share of alerted time one window must own to be named |
| `FLOOR_MARGIN` | `0.05` | `checks.py` | the section 8b margin around the predict-all floor |
| `IMPLAUSIBLE_PREVALENCE` | `0.50` | `byod.py` | prevalence above which the report warns that most of the window was an incident |
| `LONG_INCIDENT_MEDIAN_MULTIPLE` | `10.0` | `byod.py` | multiple of the median incident length for an implausibly long row |
| `LONG_INCIDENT_MIN_ROWS` | `3` | `byod.py` | minimum in-range incident rows for the long-row guard |
| `LONG_INCIDENT_RANGE_SHARE` | `0.10` | `byod.py` | share of the observation range for an implausibly long row |
| `LOW_RECALL_NOTICE` | `0.50` | `byod.py` | recall below which a `PASS` carries a low-recall notice |
| `MAX_BUCKETS` | `5_000_000` | `byod.py` | largest bucket grid the tool will build (9.5 years at 60 seconds) |
| `MIN_DISTINCT_SCORES` | `3` | `byod.py` | a score column with this many distinct values or fewer is treated as already thresholded |
| `NEAR_BAR_BAND` | `0.10` | `byod.py` | how close to the guard bar or the alert-volume limit a result must be to be reported as near it |
| `NEAR_FLOOR_BAND` | `0.20` | `byod.py` | distance of the lift from 1.0 that triggers the near-floor notice |
| `OVERLAP_NOTICE` | `0.10` | `byod.py` | share of window time under another window that triggers the overlap notice |
| `PREVALENCE_DRIFT_NOTICE` | `2.0` | `byod.py` | factor by which prevalence may move across the bucket sweep before it is named |
| `RANDOM_AUC_REFERENCE` | `0.5` | `checks.py` | random reference for AUC-ROC (section 7) |
| `RATIO_NOTICE_MULTIPLE` | `4.0` | `byod.py` | the same value as `ALERT_RATE_MULTIPLE`, used for the report notice |
| `RECALL_SATURATION` | `0.95` | `checks.py` | recall at which section 8b treats recall as saturated |
| `SCOPE_NAMESPACE_MAX_SCOPES` | `1` | `byod.py` | distinct scopes on one side that mark an empty intersection as a naming difference |
| `SCOPE_OVERLAP_POOR` | `0.50` | `byod.py` | share of alert rows on unmatched scopes that triggers the scope warning |
| `SUPPRESSION_MAX_LEVERAGE` | `50.0` | `byod.py` | how much of the alerted rate may be blamed on the bucket size |
| `SWEEP_BUCKETS` | `("1m", "5m", "15m", "1h")` | `byod.py` | the bucket sizes in the alerts-mode sweep |
| `ZERO_LENGTH_DUTY_LIMIT` | `0.20` | `byod.py` | share of point events above which the duty cycle is not used |

## A worked example

The output below is the report for the bundled `alerts_useless.csv`, a deliberately useless
detector.

```bash
$ python bin/reproduce.py check --alerts examples/alerts_useless.csv \
    --incidents examples/incidents.csv --bucket 5m --infer-range
```

```
# FDES v1.0.0-draft check report: your own data (alerts mode)

Produced by otel-aiops-reproduction v1.5.0. The specification version and the tool version are different things, and a report that names only the first cannot be traced back to the build that made it.

Verdict: **EXCLUDE**

Bucket sweep. The same two files were re-run at 1m, 5m, 15m, 1h and every bucket that could be evaluated gave EXCLUDE, so the verdict does not turn on the bucket size you picked. The table is under the checks.

Timeline 2026-03-01T02:00:00Z to 2026-03-07T11:15:00Z, 1839 buckets of 300 s. 1839 buckets were evaluated, of which 102 fall inside an incident window.

| Check | Section | Value | Reference | Result |
|---|---|---|---|---|
| Prevalence | 2 | p = 0.0555 (102 of 1839 buckets) | | reported |
| Predict-all F1 floor | 2, 7 | F1 = 0.051 | floor = 0.105 (p = 0.0555) | F1 minus floor = -0.054 |
| Lift over predict-all | 7 (this tool's rule) | F1 / floor = 0.49 | > 1.0 | EXCLUDE |
| Threshold-independent (ROC) | 6, 8a | NOT COMPUTED | 0.5 | see below |
| Threshold-independent (PR) | 6, 7 | NOT COMPUTED | p | see below |
| Range-based (VUS) | 6 | NOT COMPUTED | | see below |
| Flag-everything guard | 8b | recall = 0.078, alerted rate = 0.114 | F1 within 5% of floor and recall >= 0.95 | pass |
| Alerted rate against prevalence | 8b | alerted rate = 0.114, p = 0.0555, ratio = 2.1x, bar = 0.333 | rate^2 >= 2 x p | pass |
| Degenerate output guard | 8 | alerted rate = 0.114, distinct scores = n/a | alerts on all, alerts on none, or one near-constant score | pass |

## Operating point

Precision 0.038, recall 0.078, F1 0.051. True positives 8, false positives 202, false negatives 94, true negatives 1535.

## The verdict at other bucket sizes

The same two files, re-bucketed. A coarse bucket lets one short alert cover a whole bucket of incident time for free, so this table is the cheapest way to see whether the verdict is a property of the detector or of the bucket size.

| Bucket | Buckets | Prevalence | Alerted rate | Guard bar | Recall | F1 | Floor | F1 / floor | Decided by | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 1m | 9195 | 0.0555 | 0.114 | 0.333 | 0.078 | 0.051 | 0.105 | 0.49 | no_lift_over_predict_all | EXCLUDE |
| 5m (yours) | 1839 | 0.0555 | 0.114 | 0.333 | 0.078 | 0.051 | 0.105 | 0.49 | no_lift_over_predict_all | EXCLUDE |
| 15m | 613 | 0.0587 | 0.114 | 0.343 | 0.111 | 0.075 | 0.111 | 0.68 | no_lift_over_predict_all | EXCLUDE |
| 1h | 154 | 0.0844 | 0.136 | 0.411 | 0.154 | 0.118 | 0.156 | 0.76 | no_lift_over_predict_all | EXCLUDE |

## What this run could not compute

**Every threshold-independent rank metric (AUC-ROC, PR-AUC, VUS-PR, VUS-ROC)**

Alerts mode gets binary alert windows. An alert window is a decision, not a score, so it is already thresholded. A rank metric sweeps the threshold and measures how well the scores order the buckets. With the threshold already applied there is no ordering left to sweep. Passing an already-thresholded vector to an AUC function does return a number, and that number is a rescaling of the balanced accuracy at the one operating point reported above. It is a different quantity from a score-based AUC-ROC and it must not be printed in the same column as one, so this tool does not print it at all. To get these metrics, export the underlying anomaly score or deviation series and use scores mode.

**The FDES section 8a exclusion**

Section 8a excludes a detector whose threshold-independent score sits at or below its random reference. With no rank metric there is nothing to compare against 0.5, so this exclusion cannot fire on this input. A PASS verdict here rests on fewer checks than a PASS in scores mode, and it is not evidence that section 8a would have been passed.

## Why the verdict is EXCLUDE

1. F1 0.0513 is at or below the predict-all floor 0.1051, so flagging every bucket would have scored the same or better.

## What was assumed

1. No explicit range was given, so --infer-range set it to the span of the input, 2026-03-01T02:00:00Z to 2026-03-07T11:15:00Z. Quiet time outside that span is invisible to this run, which inflates prevalence and flatters precision. Pass --from and --to with your real observation window for a trustworthy number.
2. examples/alerts_useless.csv: timestamps read as ISO 8601, no row carried a timezone, so every timestamp was read as UTC.
3. examples/incidents.csv: timestamps read as ISO 8601, all rows carried a timezone.
4. Sub-second precision was floored away. The bucket is 300 s, so this only matters if your events are shorter than a second.
5. The incident windows were taken as exact ground truth. Postmortem and incident-tracker times usually are not. They tend to start when customer impact was noticed rather than when the signal first moved, and to end after the signal came back. A detector that fires early is charged a false positive for it, and one that stops early is charged a false negative. Tighten the windows if you know better than the tracker does.
6. Provenance was not checked. This tool cannot tell whether your incident windows were derived from the alerts being scored. If they were, the result is circular. See "Where the incident windows came from".
7. A tracker close time is a record of when somebody closed a ticket, not a record of when impact stopped. A row left open over a weekend covers the weekend. Building the incident windows from when alerting started and stopped is usually closer to the truth than the tracker is.
8. Scope was not checked. Neither file carries a column this tool recognises as a service or scope, so name one with --scope-col and --incident-scope-col if you have it. Without it every alert is scored against every incident whatever system it came from. If the alert export covers more services than the incident export, alerts on the services that have no incident in the file are counted as false positives and could not have been anything else. Precision, F1 and the verdict all move with that, and nothing in the numbers shows it.

Input `alerts`: 7 rows from examples/alerts_useless.csv, severities warning 7.
The 7 windows of `alerts` inside the range do not overlap. They cover 210 buckets in 7 distinct stretches.
Input `incidents`: 5 rows from examples/incidents.csv.
The 5 windows of `incidents` inside the range do not overlap. They cover 102 buckets in 5 distinct stretches.
```

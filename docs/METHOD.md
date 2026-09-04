# How the checks work, and why they are shaped the way they are

This is the long half of the documentation. The README tells you how to run the tool. This
file tells you what each check is doing and, more usefully, what it got wrong before it was
shaped this way.

Almost every rule below exists because a real export broke an earlier version of it. Four
production observability stacks were run through the bring-your-own-data path, on Datadog,
Splunk, Azure Monitor and Cloudflare, and one reviewer spent eight rounds building detectors
designed to walk through the checks. The `CHANGELOG.md` has the full list with dates. What
survives here is the reasoning, kept because a check whose justification is not written down
gets quietly removed by the next person who finds it inconvenient.

The pattern worth knowing before you read any of it: the verdicts held up throughout and the
explanations did not. Most of what was found was this tool reaching the right answer and
stating the wrong reason with confidence.

---

## Where the incident windows came from

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

## Getting alert data out of a real vendor

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

## Incident rows nobody closed

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

## The bucket size chooses the verdict

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

`--no-sweep` holds the reported verdict at the bucket you passed. It does not give you that
bucket's exit code back. A suppressed `UNSTABLE` still exits 4, because a flag that chooses
which verdict is printed must not also decide whether a script sees a failure.
`sweep_suppressed_unstable` in `check_result.json` tells the two apart.
The sweep is four extra runs of arithmetic on the same arrays, so it costs nothing worth
saving. `SWEEP_BUCKETS` in `fdes/byod.py` sets the ladder. The sweep is alerts mode only:
in scores mode the operating point moves with the bucket as well as the bucket boundaries,
so a sweep there would change two things at once and answer neither.

## Scope, when the two files describe different systems

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

## The alerted rate against prevalence

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

**The alerted rate is bucket occupancy, and the guard knows the difference.** A window
shorter than the bucket claims the whole bucket, so at a coarse bucket a detector that
alerts briefly and often reads exactly like one that alerts continuously. A detector
catching six one-hour incidents in full, plus six one-minute false alarms a day, is in an
alerting state 1.25 percent of the time against a prevalence of 0.83 percent, so it alerts
1.5 times as often as anything is wrong. At an hourly bucket every one-minute alarm claims a
whole bucket, the alerted rate reads 0.25, and the guard excluded it while stating that it
alerted 30 times as often as anything was wrong. That sentence was false and it was the most
confident sentence in the report.

So before the guard excludes, the same rule is applied to the raw alert and incident
durations, which no bucket has touched. If it does not fire there, the exclusion belongs to
the bucket size you picked rather than to the detector, and it is not applied. The report
says so, gives the duty cycle and the true ratio, and points you at a bucket close to your
shortest alert. It also gives the alert count and the precision, because how many times a
detector pages is a different question from how much time it occupies, and the guard settles
neither. This applies in alerts mode only, since scores mode has no durations to read.

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

**Alert volume, and what still is not measured.** Nothing in the checks above reads how
often a detector pages. The guard reads how much of the timeline it occupies, and a detector
that fires constantly in short bursts occupies very little of it, so a run can clear every
check here while paging more often than anyone would read. The report gives the alerts per
incident, the alerts per day, the window count and the incident stretch count, and says
plainly that it is not deciding for you.

Two limits on that, both raised by people who own pagers. An alert window is not a page,
because real stacks deduplicate and group, and the export does not say which it is. And the
precision reported in the check table is measured per bucket, so it is not the fraction of
pages that were worth reading.

`ALERTS_PER_INCIDENT_LIMIT` is 50 and it is a weaker number than the guard curve. The known
reference points are one detector at 31 that is worth keeping and two at 176 and 355 that are
not, plus one real production stack at 49.47, which misses the limit by about half an alert.
A run inside 10 percent of the limit says so. The denominator counts merged incident
stretches rather than rows, because row count is a formatting property of an export: the same
two outages written as forty rows instead of two used to move the ratio from 355 to 17.75 and
flip the verdict with every other number identical.

**This check does not reach scores mode.** There are no discrete alert windows in a score
series, so there is nothing to count. Threshold up-crossings would give an equivalent and
they are not implemented. A scores-mode `PASS` therefore rests on fewer checks than an
alerts-mode one, in this respect as well as in section 8a.

**On the leverage cap.** `SUPPRESSION_MAX_LEVERAGE` is not an independent gate and should not
be read as one. Leverage is roughly the bucket divided by the window, so it only answers
whether the windows are very short relative to this bucket, and it is denominated in the same
quantisation it is meant to police. It caught two degenerate exports at 300 and 370 and has
never bound on anything else. It is a floor against nonsense exports and nothing more.

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

## When prevalence is above a half, and nothing can fail

The guard's bar is the square root of twice prevalence. Above a prevalence of 0.5 that bar
exceeds 1.0, and an alerted rate cannot exceed 1.0, so the guard is structurally incapable of
firing. The docstring proved the safe direction, that a perfect detector can never be caught,
and for a while nobody wrote down the dual. A real Cloudflare export at a prevalence of 0.853
printed a bar of 1.306 next to the word `pass`, for a check that could not have failed, while
a detector alerting on 61.5 percent of the timeline walked through it. The check table now
says `not reachable at this prevalence`.

The deeper problem is that nothing else can fail there either. Section 8b needs recall at or
above 0.95. Alerts mode has no rank metric, so section 8a cannot fire. And the predict-all
floor reaches 0.92 at that prevalence, so F1 barely separates a working detector from one that
alerts on everything. A detector alerting on 88 percent of the timeline came back `PASS` at
exit 0 with not one check firing.

That is not a detector that passed. It is an input that cannot produce a verdict, and it is
reported as `INSUFFICIENT`. Two conditions are needed, because prevalence alone was too blunt:
a perfect detector at a prevalence of 0.6 scores a third above the floor, which is a real
measurement, and it keeps its `PASS`. The verdict is withheld only when the guard is
unreachable and the detector also sits inside the near-floor band. An `EXCLUDE` still stands
either way, because a check firing is evidence.

A prevalence above a half also gets its own notice at the top of the report. It means most of
the observation window was inside an incident, which is occasionally true on a range chosen
around a long outage and much more often means the incident file is describing ticket lifetime
rather than incident time.

## Duplicate alert rows, and why the tool counts them anyway

One real export delivered every notification to four email recipients. Each alert appeared
four times, with a distinct row identifier and an identical timestamp. That multiplied the
alerts per incident by four and made the overlap notice describe a distribution list as if it
were a temporal property of the detector.

The alerted rate is not affected, and the first version of this notice said it was. Occupancy
is a union, so duplicate windows claim buckets that were already claimed, and the rate is
identical at every multiplicity.

Exact-pair matching caught that case and nothing else. Real delivery fan-out does not produce
four identical timestamps, it produces four deliveries a second or two apart, and one second
of jitter defeated the check entirely while doing identical damage. So the check also compares
the row count against the number of separate stretches those rows cover, since repeated
deliveries of one alert collapse into one stretch however they are jittered.

That second signal cannot distinguish a fan-out from a genuinely bursty detector. Three real
firings a minute apart, each five minutes long, produce exactly the same shape as one alert
delivered three times. Both read three rows per stretch. The report says so rather than
guessing, and it is a notice rather than a verdict, so nothing is decided on it.

## There is no minimum recall

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

## When the lift sits near the floor

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

## Overlapping alert windows

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

## The time range, and why it is not guessed

Alerts and incidents say when something happened. They do not say when you were watching.
So `check` refuses to infer the evaluation range from them and asks for `--from` and
`--to`, printing the span of your data so you can copy it.

`--infer-range` uses that span anyway. It makes the range as tight as the events allow,
which throws away every quiet period outside them, and that inflates prevalence and
flatters precision. It is there for a quick look, and the report says loudly that it was
used.

## What it computes

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

## What alerts mode refuses to compute, and why

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

## The threshold in scores mode

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

## The constants, and where to change them

Every threshold in these checks is a named constant. None of them is a law. If your
operation has a different tolerance, change the constant rather than working around the
verdict. This table is generated from the source so it cannot drift.

| Constant | Value | File | What it is |
|---|---|---|---|
| `ALERTS_PER_INCIDENT_LIMIT` | `50.0` | `byod.py` | Alert volume, measured against the incidents there were to find. This is deliberately a ratio and not a rate per clock hour. An absolute clock... |
| `ALERT_RATE_MULTIPLE` | `4.0` | `checks.py` | Not a guard constant. the ratio worth printing, see byod |
| `ALERT_RATE_PRODUCT` | `2.0` | `checks.py` | Section 8b read on the alerted rate, see alert_rate_saturated |
| `CONCENTRATION_MIN_ROWS` | `5` | `byod.py` | See the section above. |
| `CONCENTRATION_NOTICE` | `0.50` | `byod.py` | See the section above. |
| `CONCENTRATION_TOP_FRACTION` | `0.10` | `byod.py` | The general form of the same problem: a few rows own most of the positive class, whether or not any single one of them is long enough to look broken.... |
| `CONSTANT_SPREAD_RATIO` | `1e-6` | `byod.py` | Relative spread at or below this counts as a near-constant score. |
| `DEGENERATE_AUC` | `0.55` | `checks.py` | Article rule (Table 12) |
| `DEGENERATE_F1_RATIO` | `0.95` | `checks.py` | Article rule (Table 12) |
| `DOMINANT_ALERT_MIN_ROWS` | `5` | `byod.py` | Below this the share is arithmetic, not a finding |
| `DOMINANT_ALERT_SHARE` | `0.30` | `byod.py` | One alert window owning this much alerted time gets named |
| `FLOOR_MARGIN` | `0.05` | `checks.py` | Section 8b, "stated margin" used by this package (5 percent) |
| `IMPLAUSIBLE_PREVALENCE` | `0.50` | `byod.py` | Above this, most of the window was an incident. Say so loudly |
| `LONG_INCIDENT_MEDIAN_MULTIPLE` | `10.0` | `byod.py` | See the section above. |
| `LONG_INCIDENT_MIN_ROWS` | `3` | `byod.py` | The median needs a population to be a median. Below this many in-range rows there is no distribution to be an outlier against, so the guard stays... |
| `LONG_INCIDENT_RANGE_SHARE` | `0.10` | `byod.py` | 1. It covers at least this share of the observation range. A window that spans a tenth of everything you watched is not an incident any more, it is a... |
| `LOW_RECALL_NOTICE` | `0.50` | `byod.py` | But a PASS at recall 0.071, which one real run produced from four instantaneous alerts over four days, is carried entirely by precision and by a low... |
| `MAX_BUCKETS` | `5_000_000` | `byod.py` | Refuse to build a grid larger than this. 5 million buckets is 9.5 years at 60 seconds. |
| `MIN_DISTINCT_SCORES` | `3` | `byod.py` | A score column with this many distinct values or fewer is treated as already thresholded. |
| `NEAR_BAR_BAND` | `0.10` | `byod.py` | An exclusion this close to the bar says so, see the reason text |
| `NEAR_FLOOR_BAND` | `0.20` | `byod.py` | A lift this close to 1.0 either way gets its own line near the top of the report. The verdict there turns on a rounding-level difference and it moves... |
| `OVERLAP_NOTICE` | `0.10` | `byod.py` | Overlap at or above this share of the window time gets its own line near the top of the report, because at that point the input row count no longer... |
| `PREVALENCE_DRIFT_NOTICE` | `2.0` | `byod.py` | Prevalence moving this much across the ladder gets named |
| `RANDOM_AUC_REFERENCE` | `0.5` | `checks.py` | Section 7, ROC family |
| `RATIO_NOTICE_MULTIPLE` | `ALERT_RATE_MULTIPLE` | `byod.py` | So the ratio is reported wherever it is high and the guard did not act on it. The reporting threshold is path A's multiple rather than a new number,... |
| `RECALL_SATURATION` | `0.95` | `checks.py` | Section 8b, "recall near saturation" |
| `SCOPE_ECHO_CAUTION` | `(` | `byod.py` | See the section above. |
| `SCOPE_NAMESPACE_MAX_SCOPES` | `1` | `byod.py` | Two or three scopes against thirty is suggestive of the same thing and it is not unambiguous, because two exports really can cover two small... |
| `SCOPE_OVERLAP_POOR` | `0.50` | `byod.py` | ------------------------------------------------------------------------- scope overlap Alerts and incidents can describe different systems and the... |
| `SUPPRESSION_MAX_LEVERAGE` | `50.0` | `byod.py` | How much of the rate the bucket may be blamed for |
| `ZERO_LENGTH_DUTY_LIMIT` | `0.20` | `byod.py` | Above this share of point events, duty cycle means nothing |

## A worked example

```bash
$ make check ALERTS=examples/alerts_useless.csv
```

The bundled `alerts_useless.csv` is a deliberately useless detector. Regenerated from the
current build, so it always matches what the tool actually prints.

```
# FDES v1.0.0-draft check report: your own data (alerts mode)

Produced by otel-aiops-reproduction v1.3.9. The specification version and the tool version are different things, and a report that names only the first cannot be traced back to the build that made it.

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

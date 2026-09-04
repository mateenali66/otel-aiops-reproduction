# FDES v1.0.0-draft pilot report: example_isolation_forest on logs (fold 1)

Verdict: **PASS**

| Check | Section | Value | Reference | Result |
|---|---|---|---|---|
| Predict-all F1 floor | 2, 7 | F1 = 0.580 | floor = 0.566 (p = 0.395) | F1 minus floor = +0.014 |
| Threshold-independent (ROC) | 6, 8a | AUC-ROC = 0.635 | 0.5 | pass |
| Threshold-independent (PR) | 6, 7 | PR-AUC = 0.532 | p = 0.395 | normalised lift = 0.2271 |
| Range-based (VUS) | 6 | VUS-PR = 0.533, VUS-ROC = 0.637 | buffer = 100 windows | reported |
| Flag-everything guard | 8b | recall = 0.742, predicted rate = 0.616 | F1 within 5% of floor and recall >= 0.95 | pass |
| Degenerate (article Table 12 rule) | 8 | AUC <= 0.55 and F1 >= 0.95 x floor | | not degenerate |

The threshold was selected on validation repetition [10] (section 5 step 2). Test repetitions were [1, 2]. The seed was 43 (base 42 + fold).
10449 windows were evaluated with cooldown excluded. Fitting took 0.14 s.

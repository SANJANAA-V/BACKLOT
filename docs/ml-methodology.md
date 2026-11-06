# ML methodology

**Purpose**: rank returns by how inconsistent their evidence is, to prioritise human review.
It is not a fraud detector and never decides anything on its own.

## Data
`scripts/generate_data.py::iter_mixed` produces SYNTHETIC returns (65% normal, the rest spread over 9 scenarios;
about 11% anomalous). Labels come from scenario design (wrong product, empty package, conflicting condition,
weight discrepancy = anomalous), then noise is added (10% of anomalous labelled normal, 1% of the rest labelled
anomalous). Noise rates are arbitrary. There is no real-world ground truth in this project.

## Features (`services/ml_worker/features.py`)
Computed from reconciliation metrics only, so training and serving share one code path: weight deviation,
weight missing, missing logistics count, duplicate count, identifier mismatch/unverified, how many condition
grades worse than reported, condition unknown, missing item reported, max logistics gap, prior returns (90d).
Missing values become explicit indicator features rather than silent imputation.

## Leakage controls
- No scenario name or label reaches the feature builder (tested).
- One row per return, split 60/20/20 stratified by label; splits are disjoint (tested).
- Scaling is inside the sklearn Pipeline, fit on train only.
- Model choice and decision threshold use validation only; test is scored once.
- Shuffled-label sanity test: training on permuted labels collapses test PR-AUC (tested).

## Class imbalance
`class_weight="balanced"` for both candidates; PR-AUC, precision, recall and the confusion matrix are reported
instead of accuracy. Scores are not calibrated probabilities.

## Models
Logistic regression and shallow gradient boosting; the better validation PR-AUC wins. Per-prediction
"contributing factors" use occlusion (replace one feature by its training median, measure the score change) -
a model-agnostic approximation, not an exact attribution.

## Rule baseline
Hand-written points (identifier mismatch, missing item, weight deviation bands, condition worse by >=2/>=4).
Flag if points >= 1.

## Known limitations
- Result on synthetic data: the rule baseline matches the ML model (see docs/evaluation.md). The data cannot show ML adding value.
- Incomplete evidence is labelled "not anomalous" by scenario design, so the model learns missing-evidence features as benign;
  completeness is a separate input to the recovery decision.
- The model file is a pickle (joblib): load only files you trained.
- Not evaluated for fairness or drift; synthetic data has no customer demographics.

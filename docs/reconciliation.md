# Evidence reconciliation

`services/evidence_worker/reconciler.py::reconcile(return_id, events, duplicate_counts)` is a pure function:
same events in any order -> identical result. It never calls a model.

## Severity vs confidence
- **Severity**: how much it would matter if the discrepancy is real (INFO/LOW/MEDIUM/HIGH).
- **Confidence**: confidence the discrepancy is a real-world difference rather than a data error (LOW/MEDIUM/HIGH).
  Example: an identifier mismatch is HIGH severity but only MEDIUM confidence (scanner/typo errors happen).
- Condition conflicts drop to LOW confidence when there is no warehouse photo to corroborate them.
- Every finding lists `benign_explanations` and cites `supporting_event_ids`.
No finding is an accusation; the output carries a disclaimer to that effect.

## Checks and thresholds (module constants)
| Check | Rule |
|---|---|
| Missing events | 9 expected items (incl. customer + warehouse image). Critical missing -> INSUFFICIENT, other -> PARTIAL |
| Redelivery duplicates | same event_id delivered more than once (LOW) |
| Semantic duplicates | same type + payload + source within 10 min, different event_id (LOW) |
| Sequence | lifecycle stage timestamped before its predecessor (LOW, clock skew likely) |
| Delay | >72h between consecutive logistics events (LOW) |
| Weight | deviation <=5% none; <=25% LOW; <=50% MEDIUM; >50% HIGH. Uses first weight record; a second record differing >5% -> CONFLICTING_WEIGHTS |
| Identifier | expected != observed -> HIGH / MEDIUM confidence |
| Condition | rank scale unopened<like_new<good<used<used_with_damage<damaged. Observed worse by >=2 -> MEDIUM (>=4 HIGH); better by >=2 -> LOW. Unknown labels -> INFO, never a conflict |
| Damage origin | damage observed -> origin (pre-pickup vs transit) flagged uncertain; LOW if customer disclosed it, else MEDIUM |

These thresholds are my judgment calls for a demo, not tuned on real data.

## Evidence graph
Nodes: return, events, findings. Edges: `belongs_to`, `precedes` (timeline), `about`, `supported_by`
(finding -> event), `conflicts_with` (event <-> event for condition/missing-item conflicts).

## Pipeline
`evidence_worker` persists each event, then recomputes the whole return from stored events and upserts a
snapshot. Because it recomputes from storage, out-of-order arrival converges to the same result.
`GET /returns/{id}/reconciliation` serves the snapshot (eventually consistent); `?live=true` recomputes.

## Not done yet
Image comparison (Phase 5), inter-event semantic checks beyond those above, per-category weight tolerances.

# BACKLOT

**The intelligence layer behind product returns and recovery.**

_Distributed multimodal return-intelligence and product-recovery system._

An event-driven system that reconstructs an e-commerce return's journey from independent services, finds
inconsistencies in the evidence, and recommends a product-recovery action for a **human reviewer**.
It never accuses anyone of fraud and never finalises a decision on its own.

> **All data is synthetic.** The risk model is trained on synthetic data with scenario-derived labels; its metrics say
> nothing about real fraud detection. Image analysis in the demo is simulated. See *Limitations*.

## Problem
Return evidence lives in separate systems (orders, pickup scans, parcel weight, photos, warehouse inspection) and is often
incomplete, duplicated, late or contradictory. Reviewers need the evidence assembled, discrepancies explained with caveats,
and a defensible, auditable recommendation.

## Features
- Kafka/Redpanda pipeline: partition by `return_id`, schema validation, idempotent consumers, bounded retries with backoff, dead-letter topic.
- Evidence reconciliation engine: timeline, missing/duplicate/delayed/out-of-sequence events, weight and identifier checks,
  condition conflicts, evidence graph, severity **and** confidence per finding, every finding cites event ids.
- ML risk score (scikit-learn) with a rule-based baseline, leakage controls and honest evaluation (`docs/evaluation.md`).
- Replaceable vision adapters (simulated offline; Anthropic API with retries/rate-limit handling) that label output as unverified.
- Policy RAG over versioned sample policies + LLM investigation report validated against the evidence, with deterministic fallback.
- Transparent decision engine (RESALE / REFURBISH / REPAIR / LIQUIDATE / RECYCLE / HUMAN_REVIEW) with a rule trace.
- Streamlit investigation UI, human review workflow, append-only audit history.

## Quick start (no Docker, no API keys)
```
pip install -r requirements.txt
make train        # trains the synthetic-data risk model, writes models/ and docs/evaluation.md
make test
make demo         # Streamlit demo: 10 synthetic sample cases, simulated vision, deterministic report
```
Python 3.12 was used. The demo seeds an in-memory database; it does not use Kafka or Postgres.

## Full stack with Docker Compose
```
cp .env.example .env     # optional: set ANTHROPIC_API_KEY to enable the LLM report
make up                  # redpanda, postgres, redis, gateway, evidence worker, api, ui
python -m scripts.generate_data            # on the host, or run inside the api container
python -m scripts.replay_events --gateway http://localhost:8000
```
UI: http://localhost:8501, API: http://localhost:8001/docs, gateway: http://localhost:8000/docs.
**The Compose file, Dockerfile and Kafka/Redpanda/Postgres/Redis code paths have not been run by the author** (no Docker or broker
was available). Expect to debug the first `make up`.

## Event workflow
1. A source service POSTs a `ReturnEvent` to the gateway (422 if invalid, 503 if the bus is down, 202 if accepted).
2. The event is published to `return-events` keyed by `return_id`.
3. The evidence worker consumes it: ledger check (idempotency) -> store -> recompute the return's reconciliation snapshot.
   Failures retry with exponential backoff, then go to `return-events.dlq`. Offsets commit after processing.
4. `POST /returns/{id}/investigate` runs reconciliation, risk, vision, decision and the report, recording an audit entry per step.
See `diagrams/` and `docs/event-flow.md`.

## Stack
Python, FastAPI, Pydantic, SQLAlchemy (PostgreSQL / SQLite for tests), Kafka-compatible broker (confluent-kafka), Redis (vision cache),
scikit-learn, httpx (Anthropic API), Streamlit, Docker Compose, GitHub Actions.

## Repository structure
```
services/  event_gateway, evidence_worker (+reconciler), ml_worker, vision_worker, investigation_worker, recovery_worker, api
shared/    schemas, database, messaging, utilities
frontend/  Streamlit app + demo bootstrap     scripts/  data generation, training, topics, replay
data/      policies (sample), samples         docs/  architecture, event flow, ML, LLM/RAG, failures, threats, evaluation
tests/     unit, integration                  diagrams/ Mermaid
```

## Sample API usage (real output from the demo data)
```
POST /returns/R-00007/investigate      # R-00007 = synthetic "conflicting condition" case
-> decision.action: HUMAN_REVIEW, human_review_required: true
   reasons: ["The risk model or a high-severity evidence finding calls for review.",
             "Image analysis reported differences between photos."]
   risk: score 0.986, flagged true
   top finding: CONDITION_CONFLICT, HIGH severity, MEDIUM confidence,
                "Customer reported 'unopened' but warehouse observed 'used_with_damage'"
   vision difference: "Damage is visible in the warehouse image but not in the customer image"
   policy refs: RP-5, WI-4, WI-1, RP-2;  report generated_by: deterministic_fallback;  review_status: PENDING
```
Other endpoints: `GET /returns`, `GET /returns/{id}/reconciliation[?live=true]`, `GET /returns/{id}/risk`, `POST /risk/score`,
`GET /returns/{id}/investigation`, `GET /returns/{id}/audit`, `POST /returns/{id}/review`.

## Demo walkthrough
`make demo`, pick **R-00002 (wrong product)** -> Run investigation: completeness COMPLETE, an `IDENTIFIER_MISMATCH` finding
(HIGH severity, MEDIUM confidence), risk flagged, recommendation HUMAN_REVIEW (review status PENDING). Open the tabs
(Timeline + evidence graph, Findings, Image evidence, Policies, Report, Decision with rule trace, Audit), then record a reviewer
decision in the Decision tab. Compare with **R-00001 (normal)** -> a resale/refurbish recommendation with no review needed.
No screenshots are included.

## ML methodology
Details in `docs/ml-methodology.md`; results in `docs/evaluation.md` (regenerated by `make train`).
One row per return, stratified 60/20/20 split, scaler inside the pipeline, model + threshold chosen on validation only, test scored once,
balanced class weights, precision/recall/F1/PR-AUC/confusion matrix, shuffled-label sanity test. **On this synthetic data a hand-written
rule baseline matches the ML model** - the labels are defined by the same logic that creates the features.

## Reliability and failure handling
`docs/failure-handling.md`: idempotent consumers, retries, DLQ, redelivery after crashes, vision/LLM degradation, grounded-output validation.

## Limitations and ethical considerations
- Synthetic data only; no real-world validation of any component. Thresholds and demo economics are arbitrary.
- Simulated vision does not look at pixels; the real adapter was only tested with a mocked API. No serial-number recognition or authenticity checks exist.
- TF-IDF retrieval is lexical. LLM grounding checks are heuristic.
- Kafka, Postgres, Redis, Docker, CI Kafka job: written but not executed by the author.
- No authentication, rate limiting or PII handling. Not production-ready.
- Scores can create automation bias; the system is designed so a person decides, and decisions stay auditable.

## Future work
Event-driven investigation chaining (derived topic), DLQ replay tool, real image evaluation set, calibrated models on real labelled data,
fairness evaluation, authentication, observability, load testing.

## Tests
`make test`. Covers schemas, duplicates, idempotency, out-of-order events, reconciliation, risk inference, RAG, LLM validation and fallback,
decision rules, API endpoints, retries/DLQ, vision failures, and the Streamlit app (headless). The real-Kafka test is skipped unless
`BACKLOT_KAFKA_IT=1` and a broker is running.

License: MIT.

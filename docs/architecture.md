# Architecture

See `diagrams/system-architecture.mmd` and `diagrams/event-sequence.mmd` (Mermaid).

## Components
| Component | Path | Role |
|---|---|---|
| Event gateway | `services/event_gateway` | Validates `ReturnEvent`, publishes to Kafka keyed by `return_id` |
| Messaging | `shared/messaging` | Broker interface, Kafka + in-memory brokers, idempotent processor (retries, DLQ) |
| Evidence worker | `services/evidence_worker` | Consumer group: persist event, recompute reconciliation snapshot |
| Reconciler | `services/evidence_worker/reconciler.py` | Pure function: timeline, findings, completeness, evidence graph |
| Risk model | `services/ml_worker` | Features, training, inference (synthetic-trained) |
| Vision | `services/vision_worker` | Adapter interface: simulated (offline) or Anthropic API; cross-image comparison |
| Decision engine | `services/recovery_worker` | Deterministic, traceable business rules + demo economics |
| Investigation | `services/investigation_worker` | Policy RAG, grounded LLM report, deterministic fallback, orchestrator + audit |
| API | `services/api` | Reconciliation, risk, investigate, review, audit endpoints |
| UI | `frontend` | Streamlit; standalone demo mode or real API |

## Design decisions and trade-offs
- **Evidence engine and decision engine are plain code, not LLM calls.** The LLM only explains their output.
- **Kafka delivery is at-least-once;** idempotency lives in a (consumer_group, event_id) ledger and event_id-keyed storage.
- **Reconciliation recomputes from storage on every event.** O(events per return), which is tiny here, and makes out-of-order arrival a non-issue. It would not suit returns with thousands of events.
- **Investigation runs on demand in the API,** not as a Kafka consumer. Chaining it behind the evidence worker needs a derived topic and a generalised processor (not built).
- **Postgres** is the store in Compose; tests use in-memory SQLite. **Redis** only caches vision results (optional; in-memory otherwise).
- **No vector database:** 16 policy chunks are searched by brute-force cosine similarity.

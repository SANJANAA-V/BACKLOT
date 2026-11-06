# Event flow

1. A source service POSTs a `ReturnEvent` to the gateway (`POST /events`).
2. The gateway validates it (schema, event type, required payload keys, schema major version).
   Invalid -> 422, never published. Bus down -> 503 so the caller retries.
3. Valid events are published to `return-events`, keyed by `return_id`.
4. Each consumer group reads independently. `evidence_worker` persists events to the `events` table.
5. Offsets are committed only after a message is processed or dead-lettered.

## Ordering
Kafka orders messages within a partition. Same key -> same partition, so events for one return that a
single producer sends are read in publish order. There is no ordering across different services' producers,
retries, or network paths, so the system never trusts arrival order: reconstruction sorts by
`event_timestamp`. (Clock skew between services is a known limitation.)

## Idempotency
`processed_events` has a unique (consumer_group, event_id). A redelivered event is skipped by that group,
and `evidence_worker` increments `duplicate_deliveries` on the stored event so reconciliation can report
duplicates. Storage is also keyed by event_id, so a second copy can never create a second row.

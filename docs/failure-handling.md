# Failure handling (Phase 2)

| Failure | Behaviour | Trade-off |
|---|---|---|
| Broker down at gateway | 503, caller retries | No local buffer: events are lost if the caller does not retry |
| Producer retry duplicates | Idempotent producer + consumer ledger | Ledger lookup per message |
| Worker crash before commit | Message redelivered; handler idempotent | Reprocessing work |
| Crash between handler commit and ledger write | Reprocessed once more; safe because handlers are idempotent | Handlers MUST stay idempotent |
| Transient handler error | In-process retries with exponential backoff (default 3 attempts) | Blocks that partition during backoff |
| Persistent handler error | Dead-letter topic with error, attempts, source offset | Needs a human/tool to inspect and replay DLQ |
| Malformed message | Straight to DLQ, no retries | - |

Not implemented yet: DLQ replay tool, retry topics (non-blocking retries), schema registry.

## Added in Phases 3-8
| Failure | Behaviour |
|---|---|
| Vision API rate limit (429/503/529) | Retry with `retry-after` or exponential backoff (max 3), then image marked `UNAVAILABLE`; investigation continues |
| Vision auth/other API error | Image marked `ERROR` with a short message (no secrets); investigation continues |
| Vision output not valid JSON | `ERROR`; never guessed |
| LLM unavailable, invalid JSON, schema violation, or ungrounded output | Deterministic report is served; `report_meta.fallback_reason` says why |
| LLM tries to change the recommended action or review flag | Rejected -> deterministic report |
| Risk model file missing | Risk step skipped and audited; decision engine discloses the score was not used |
| Image path outside the image directory / remote URL | Refused (`UNAVAILABLE`) |
| Redis unavailable | Only matters if `REDIS_URL` is set; there is no fallback logic yet, so a Redis outage would fail vision caching |

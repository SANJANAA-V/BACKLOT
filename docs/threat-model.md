# Threat model (lightweight)

| Threat | Mitigation | Residual risk |
|---|---|---|
| Customer wrongly accused by a model | Findings are labelled inconsistencies with benign explanations; HUMAN_REVIEW gate; no autonomous refund denial; audit trail | Reviewers may over-trust scores (automation bias) |
| LLM invents evidence/policy/numbers | Grounding checks + deterministic fallback | Heuristic checks miss subtle fabrications in prose |
| Prompt injection via event payloads or policy text | LLM output is validated and cannot change the decision; LLM has no tools | Misleading wording inside a valid report is possible |
| API key leakage | Keys only from environment; never in bodies, logs or error strings (tested) | Deployer must protect env/secrets |
| SSRF / path traversal via `image_uri` | Remote URIs never fetched; local paths confined to the image root (tested) | - |
| Malicious event flood / replay | Schema validation, idempotent processing | No authentication, rate limiting or signing on the gateway or API |
| Pickle model file | Documented: load only self-trained files | Supply-chain risk if the file is replaced |
| Personal data | Synthetic data only; no PII fields | A real deployment needs retention, access control and consent design |
| Biased scores | Features are evidence-based, no demographics | Not evaluated; real data may encode bias via history features |

**Not implemented:** authentication/authorisation, TLS, rate limiting, secret management, PII handling.

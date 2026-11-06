# LLM and RAG

## Retrieval
- Corpus: `data/policies/*.md` (SAMPLE, fictional, versioned via a `Version:` line); one chunk per `##` section with a stable id (RP-3, WI-5, ...).
- Default embedder: TF-IDF (unigrams+bigrams). This is **lexical**, not a semantic embedding model; it works offline and deterministically.
  `SentenceTransformerEmbedder` is an optional drop-in but was **not run** (needs a model download).
- Query = finding types + summaries + completeness + condition + image differences. Known weakness: "damaged in transit" ranks RP-5 third.

## Grounded generation
The prompt gives the model the timeline, findings (with ids), decision, risk and retrieved policy sections, and tells it to explain,
not decide. The reply must be JSON matching `InvestigationReport`. Before use it is checked:
- every event id, finding id and policy id exists in the supplied context;
- `recommended_action` and `human_review_required` equal the decision engine's;
- any percentage it states appears in the evidence (a heuristic; it does not catch other invented numbers or prose claims).
Any failure -> deterministic report built from the same facts, with the reason recorded.

## Not verified
The Anthropic client and both adapters were tested against a mocked HTTP transport only. No call to the real API was made, so
real-model output quality, prompt effectiveness and the default model names are unverified.

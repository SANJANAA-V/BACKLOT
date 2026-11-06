"""Small versioned-policy RAG. Default embedder is TF-IDF (lexical, offline, deterministic) - it is NOT a
semantic embedding model. SentenceTransformerEmbedder is an optional drop-in (untested here: needs a model download)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from shared.schemas.evidence import ReconciliationResult
from shared.schemas.vision import VisionReport

POLICY_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"


@dataclass(frozen=True)
class PolicyChunk:
    chunk_id: str
    title: str
    doc: str
    version: str
    text: str


def load_policies(directory: Path = POLICY_DIR) -> list[PolicyChunk]:
    chunks: list[PolicyChunk] = []
    for f in sorted(directory.glob("*.md")):
        raw = f.read_text()
        doc = raw.splitlines()[0].lstrip("# ").strip()
        m = re.search(r"^Version:\s*(\S+)", raw, re.M)
        version = m.group(1) if m else "unknown"
        for sec in re.split(r"^## ", raw, flags=re.M)[1:]:
            head, _, body = sec.partition("\n")
            cid, _, title = head.partition(" ")
            chunks.append(PolicyChunk(cid, title.strip(), doc, version, body.strip()))
    return chunks


class Embedder(Protocol):
    def fit(self, texts: list[str]) -> None: ...
    def encode(self, texts: list[str]) -> np.ndarray: ...  # rows are L2-normalised


class TfidfEmbedder:
    def __init__(self) -> None:
        self.v = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english")

    def fit(self, texts: list[str]) -> None:
        self.v.fit(texts)

    def encode(self, texts: list[str]) -> np.ndarray:
        return self.v.transform(texts).toarray()


class SentenceTransformerEmbedder:  # optional; requires `pip install sentence-transformers` and a downloaded model
    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer
        self.m = SentenceTransformer(model)

    def fit(self, texts: list[str]) -> None:
        pass

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.m.encode(texts, normalize_embeddings=True))


class Retriever:
    """Brute-force cosine search over a handful of chunks (a vector index such as FAISS is unnecessary at this size)."""
    def __init__(self, chunks: list[PolicyChunk], embedder: Embedder | None = None) -> None:
        self.chunks, self.emb = chunks, embedder or TfidfEmbedder()
        texts = [f"{c.title}. {c.text}" for c in chunks]
        self.emb.fit(texts)
        self.mat = self.emb.encode(texts)

    def search(self, query: str, k: int = 4) -> list[tuple[PolicyChunk, float]]:
        if not query.strip():
            return []
        sims = (self.mat @ self.emb.encode([query])[0]).tolist()
        order = sorted(range(len(sims)), key=lambda i: (-sims[i], self.chunks[i].chunk_id))
        return [(self.chunks[i], round(sims[i], 4)) for i in order[:k] if sims[i] > 0]


def default_retriever() -> Retriever:
    return Retriever(load_policies())


def build_query(result: ReconciliationResult, vision: VisionReport | None) -> str:
    parts = [f"{f.type.value.replace('_', ' ').lower()} {f.summary}" for f in result.findings]
    parts.append(f"evidence completeness {result.completeness.status} missing " + " ".join(result.completeness.missing))
    if result.facts.get("observed_condition"):
        parts.append(f"condition {result.facts['observed_condition']} grade")
    if vision and vision.differences:
        parts.append("photographs image differences " + " ".join(d.detail for d in vision.differences))
    return " ".join(parts)

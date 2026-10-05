"""Rank indexed pipelines against a researcher's problem description with BM25.

No MCP and no HTTP in here: it takes index entries in and gives ranked hits out, so it can be tested and evaluated (M5) on its own.
"""

import re
from typing import TypedDict

from rank_bm25 import BM25Okapi

from nfcore_mcp.index import IndexEntry

# The model chooses k, so it is untrusted input: cap it to keep a response readable.
MAX_HITS = 10


class PipelineHit(TypedDict):
    name: str
    release: str
    description: str
    score: float
    matching_lines: list[str]


def tokenize(text: str) -> list[str]:
    """Split text into lowercase search tokens.

    Documents and queries must go through exactly this function. Start simple (lowercase, split on non-alphanumerics); later, consider stripping Markdown badges and URLs from READMEs and dropping very short tokens.
    """
    return re.findall(r"[a-z0-9]+", text.lower())


def document_text(entry: IndexEntry) -> str:
    """The text BM25 scores for one pipeline: name, description, topics and README joined together.

    Worth experimenting with: repeating name and description a few times weights them above the README.
    """
    return " ".join([entry["name"], entry["description"], " ".join(entry["topics"]), entry["readme"] or ""])


def matching_lines(text: str, query_weights: dict[str, float], max_lines: int = 3) -> list[str]:
    """The lines of text that best match the query, so the model sees why a pipeline matched.

    query_weights maps each query token to how much it counts. Passing BM25's IDF here means a line wins on rare words like "viral", not on common ones like "and".
    """
    scored_lines = []
    for line in text.splitlines():
        if line.strip():
            score = sum(query_weights.get(token, 0.0) for token in set(tokenize(line)))
            scored_lines.append((line, score))
    scored_lines.sort(key=lambda pair: pair[1], reverse=True)
    return [line for line, score in scored_lines if score > 0][:max_lines]


class PipelineSearch:
    """A BM25 index over a fixed list of pipelines. Build once, query many times."""

    def __init__(self, entries: list[IndexEntry]) -> None:
        self.entries = entries
        self.bm25 = BM25Okapi([tokenize(document_text(e)) for e in entries])

    def search(self, problem: str, k: int = 5) -> list[PipelineHit]:
        """Return the k best-scoring pipelines for a problem description, best first. k is clamped to 1..MAX_HITS."""
        k = max(1, min(k, MAX_HITS))
        query_tokens = set(tokenize(problem))
        query_weights = {token: self.bm25.idf.get(token, 0.0) for token in query_tokens}
        scores = self.bm25.get_scores(list(query_tokens))
        scored_entries = sorted(zip(self.entries, scores), key=lambda pair: pair[1], reverse=True)
        hits: list[PipelineHit] = []
        for entry, score in scored_entries[:k]:
            hits.append(
                PipelineHit(
                    name=entry["name"],
                    release=entry["release"],
                    description=entry["description"],
                    score=score,
                    matching_lines=matching_lines(entry["readme"] or "", query_weights),
                )
            )
        return hits

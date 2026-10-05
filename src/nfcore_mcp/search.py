"""Rank indexed pipelines against a researcher's problem description with BM25.

No MCP and no HTTP in here: it takes index entries in and gives ranked hits out, so it can be tested and evaluated (M5) on its own.
"""

import re
from typing import TypedDict

from rank_bm25 import BM25Okapi

from nfcore_mcp.index import IndexEntry


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
    # TODO: re.findall(r"[a-z0-9]+", text.lower()) is a fine first version
    raise NotImplementedError


def document_text(entry: IndexEntry) -> str:
    """The text BM25 scores for one pipeline: name, description, topics and README joined together.

    Worth experimenting with: repeating name and description a few times weights them above the README.
    """
    # TODO: join entry["name"], entry["description"], " ".join(entry["topics"]), entry["readme"]
    raise NotImplementedError


def matching_lines(text: str, query_tokens: set[str], max_lines: int = 3) -> list[str]:
    """The lines of text that share the most tokens with the query, so the model sees why a pipeline matched."""
    # TODO: score each non-empty line by len(set(tokenize(line)) & query_tokens), keep the top max_lines with score > 0
    raise NotImplementedError


class PipelineSearch:
    """A BM25 index over a fixed list of pipelines. Build once, query many times."""

    def __init__(self, entries: list[IndexEntry]) -> None:
        self.entries = entries
        # TODO: self.bm25 = BM25Okapi([tokenize(document_text(e)) for e in entries])

    def search(self, problem: str, k: int = 5) -> list[PipelineHit]:
        """Return the k best-scoring pipelines for a problem description, best first."""
        # TODO:
        # 1. tokens = tokenize(problem)
        # 2. scores = self.bm25.get_scores(tokens)  -> one float per entry, same order as self.entries
        # 3. indices of the k highest scores: sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        # 4. a PipelineHit per index; float(score), because numpy floats do not serialise to JSON
        raise NotImplementedError

"""Build index/pipelines.json from the shortlist, and load it back.

Building is a command (`uv run nfcore-mcp-build-index`), not an MCP tool: it runs once, or when a new release lands, and the server only reads its output.
"""

import json
from pathlib import Path
from typing import TypedDict

import httpx

from nfcore_mcp import catalog
from nfcore_mcp.config import INDEX_FILE, SHORTLIST_FILE


class IndexEntry(TypedDict):
    name: str
    release: str
    description: str
    topics: list[str]
    readme: str


def read_shortlist(path: Path) -> list[str]:
    """Return the pipeline names in the shortlist, skipping blank lines and # comments."""
    # TODO: read lines, strip them, drop empty ones and ones starting with "#"
    raise NotImplementedError


def build_entry(client: httpx.Client, pipeline: dict) -> IndexEntry:
    """Turn one catalog entry into one index entry, fetching its README at the latest release."""
    # TODO: catalog.latest_release, then catalog.fetch_pipeline_file(..., "README.md"); return an IndexEntry
    raise NotImplementedError


def build_index(shortlist_path: Path, index_path: Path) -> list[IndexEntry]:
    """Fetch the catalog, build an entry for every shortlisted pipeline, and write them to index_path."""
    # TODO:
    # 1. names = read_shortlist(shortlist_path)
    # 2. with httpx.Client(timeout=30, follow_redirects=True) as client: by_name = {p["name"]: p for p in catalog.fetch_catalog(client)}
    # 3. fail loudly on names not in by_name (catches typos in the shortlist)
    # 4. skip archived pipelines and ones without a release, printing which and why
    # 5. build_entry for the rest, printing progress
    # 6. index_path.parent.mkdir(parents=True, exist_ok=True); write JSON; return the entries
    raise NotImplementedError


def load_index(path: Path) -> list[IndexEntry]:
    """Read the index written by build_index. Raise a clear error when it is missing."""
    # TODO: if not path.exists(): raise FileNotFoundError(f"{path} not found; run `uv run nfcore-mcp-build-index` first")
    raise NotImplementedError


def main() -> None:
    """Entry point for `uv run nfcore-mcp-build-index`."""
    entries = build_index(SHORTLIST_FILE, INDEX_FILE)
    print(f"Wrote {len(entries)} pipelines to {INDEX_FILE}")

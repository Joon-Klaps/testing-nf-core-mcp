"""Build index/pipelines.json from the shortlist, and load it back.

Building is a command (`uv run nfcore-mcp-build-index`), not an MCP tool: it runs once, or when a new release lands, and the server only reads its output.
"""

import asyncio
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
    readme: str | None


def read_shortlist(path: Path) -> list[str]:
    """Return the pipeline names in the shortlist, skipping blank lines and # comments."""
    with open(path, "r") as f:
        lines = f.readlines()
    names = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    return names


async def build_entry(client: httpx.AsyncClient, pipeline: dict) -> IndexEntry:
    """Turn one catalog entry into one index entry, fetching its README at the latest release."""
    release = catalog.latest_release(pipeline)
    if not release:
        raise ValueError(f"Pipeline {pipeline.get('name', 'unknown')} has no valid release")
    readme = await catalog.fetch_pipeline_file(client, pipeline["name"], release, "README.md")
    if not readme:
        raise ValueError(f"Pipeline {pipeline.get('name', 'unknown')} has no README.md")
    return IndexEntry(
        name=pipeline["name"],
        release=release,
        description=pipeline.get("description", ""),
        topics=pipeline.get("topics", []),
        readme=readme,
    )


async def build_index(shortlist_path: Path, index_path: Path) -> list[IndexEntry]:
    """Fetch the catalog, build an entry for every shortlisted pipeline, and write them to index_path."""
    names = read_shortlist(shortlist_path)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        by_name = await catalog.fetch_catalog(client)

        # A name missing from the catalog is a typo in the shortlist: stop rather than silently index fewer pipelines.
        unknown = [name for name in names if name not in by_name]
        if unknown:
            raise ValueError(f"Not in the nf-core catalog (typo in {shortlist_path.name}?): {', '.join(unknown)}")

        # Decide what to skip from catalog metadata alone, before any README is downloaded.
        to_build = []
        for name in names:
            pipeline = by_name[name]
            if pipeline.get("archived"):
                print(f"skip {name}: archived")
            elif catalog.latest_release(pipeline) is None:
                print(f"skip {name}: no release, only dev")
            else:
                to_build.append(pipeline)

        # Fetch all READMEs concurrently. return_exceptions=True turns a failure into a value in the results list, so one bad pipeline does not cancel the others.
        results = await asyncio.gather(*(build_entry(client, p) for p in to_build), return_exceptions=True)

    entries: list[IndexEntry] = []
    for pipeline, result in zip(to_build, results):
        if isinstance(result, BaseException):
            print(f"skip {pipeline['name']}: {result}")
        else:
            print(f"ok   {result['name']} {result['release']}")
            entries.append(result)

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(entries, indent=2))
    return entries


def load_index(path: Path) -> list[IndexEntry]:
    """Read the index written by build_index. Raise a clear error when it is missing."""
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `uv run nfcore-mcp-build-index` first")
    with open(path, "r") as f:
        return [IndexEntry(**entry) for entry in json.load(f)]


def main() -> None:
    """Entry point for `uv run nfcore-mcp-build-index`."""
    entries = asyncio.run(build_index(SHORTLIST_FILE, INDEX_FILE))
    print(f"Wrote {len(entries)} pipelines to {INDEX_FILE}")

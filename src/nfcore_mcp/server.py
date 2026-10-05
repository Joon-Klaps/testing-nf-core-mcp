"""The MCP surface: tools and resources, and nothing else.

Each tool is a few lines of glue: check the request (guardrails), call the module that does the work, shape the answer for the model.
"""

import json
from functools import cache
from typing import Literal

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from nfcore_mcp import catalog, guardrails
from nfcore_mcp.config import INDEX_FILE
from nfcore_mcp.index import IndexEntry, load_index
from nfcore_mcp.search import PipelineHit, PipelineSearch

DOC_PATHS = {"readme": "README.md", "usage": "docs/usage.md", "output": "docs/output.md"}

mcp = MCPServer("nf-core-mcp", "0.1.0")


@cache
def pipelines() -> list[IndexEntry]:
    """The index, loaded on first use and kept for the life of the server."""
    return load_index(INDEX_FILE)


@cache
def pipeline_search() -> PipelineSearch:
    """The BM25 index, built on first use and kept for the life of the server."""
    return PipelineSearch(pipelines())


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def search_pipelines(problem: str, k: int = 5) -> list[PipelineHit]:
    """Find nf-core pipelines that fit a researcher's data problem.

    Describe the data and the goal in plain words, for example "paired-end Illumina reads from a viral outbreak, want consensus genomes". Returns the k best candidates with the README lines that matched; read those before recommending one.
    """
    return pipeline_search().search(problem, k)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_pipeline_docs(name: str, release: str, doc: Literal["readme", "usage", "output"] = "usage") -> str:
    """Fetch one documentation page of an nf-core pipeline at a pinned release.

    `usage` explains inputs and parameters, `output` describes the result folders, `readme` is the overview.
    """
    entry = guardrails.check_pipeline(name, pipelines())
    guardrails.check_release(entry, release)

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            text = await catalog.fetch_pipeline_file(client, name, release, DOC_PATHS[doc])
    except catalog.CatalogError as e:
        # Not a refusal: the request was fine, GitHub was not. Say so, so the model can tell the user instead of guessing.
        raise ToolError(f"Could not fetch the docs from GitHub, try again later. Details: {e}") from e

    if text is None:
        return f"nf-core/{name} {release} has no {DOC_PATHS[doc]}"
    return text


@mcp.resource("nfcore://catalog", mime_type="application/json")
def catalog_resource() -> str:
    """Every indexed pipeline with its release, description and topics (README text left out)."""
    return json.dumps(
        [
            {"name": p["name"], "release": p["release"], "description": p["description"], "topics": p["topics"]}
            for p in pipelines()
        ]
    )


def main() -> None:
    """Entry point for `uv run nfcore-mcp`: serve over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

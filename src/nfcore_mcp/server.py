"""The MCP surface: tools and resources, and nothing else.

Each tool is a few lines of glue: check the request (guardrails), call the module that does the work, shape the answer for the model.
"""

import json
from functools import cache
from typing import Literal, TypedDict

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from nfcore_mcp import catalog, guardrails, runs, samplesheet, schemas, summary, validation
from nfcore_mcp.audit import audited
from nfcore_mcp.config import (
    ALLOWED_DATA_DIRS,
    ALLOWED_PROFILES,
    INDEX_FILE,
    RUNS_DIR,
    SAMPLESHEET_DIR,
    SCHEMA_CACHE_DIR,
)
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


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@audited
def search_pipelines(problem: str, k: int = 5) -> list[PipelineHit]:
    """Find nf-core pipelines that fit a researcher's data problem.

    Describe the data and the goal in plain words, for example "paired-end Illumina reads from a viral outbreak, want consensus genomes". Returns the k best candidates with the README lines that matched; read those before recommending one.
    """
    return pipeline_search().search(problem, k)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@audited
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


class SchemaSummary(TypedDict):
    name: str
    release: str
    required_params: list[schemas.ParamInfo]
    samplesheet_columns: list[schemas.ColumnInfo]


class ValidationResult(TypedDict):
    valid: bool
    errors: list[str]
    validation_id: str | None


async def pipeline_schemas(name: str, release: str) -> schemas.PipelineSchemas:
    """Check name and release, then load both schemas. Shared by the three M2 tools so the checks cannot be forgotten in one of them."""
    entry = guardrails.check_pipeline(name, pipelines())
    guardrails.check_release(entry, release)

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            return await schemas.load_schemas(client, name, release, SCHEMA_CACHE_DIR)
    except catalog.CatalogError as e:
        # Not a refusal: the request was fine, GitHub was not. Say so, so the model can tell the user instead of guessing.
        raise ToolError(f"Could not fetch the schemas from GitHub, try again later. Details: {e}") from e

@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@audited
async def get_pipeline_schema(name: str, release: str) -> SchemaSummary:
    """What a pipeline needs before it can run: its required parameters, and the columns of its samplesheet with which are required and what file names they accept.

    Call this before generate_samplesheet, to see which extra columns (for example strandedness) the samplesheet needs.
    """
    s = await pipeline_schemas(name, release)
    return SchemaSummary(
        name=name,
        release=release,
        required_params=schemas.required_params(s["params"]),
        samplesheet_columns=schemas.samplesheet_columns(s["input"]) if s["input"] else []
    )

@mcp.tool()
@audited
async def generate_samplesheet(
    name: str, release: str, input_dir: str, extra_columns: dict[str, str] | None = None
) -> samplesheet.SamplesheetReport:
    """Write a samplesheet for a pipeline from a folder of FASTQ files (.fastq.gz or .fq.gz).

    Read 1 and read 2 are paired by file name (_R1/_R2 or _1/_2). extra_columns sets columns the file names cannot tell, with one value for every sample, for example {"strandedness": "auto"}. The report lists required columns still missing; ask the user for those, never guess them. Nothing is written while there are errors.
    """
    s = await pipeline_schemas(name, release);
    if s["input"] is None:
        raise ToolError(f"nf-core/{name} {release} has no samplesheet schema, so a samplesheet cannot be generated.")

    folder = guardrails.check_path(input_dir, ALLOWED_DATA_DIRS)
    out_path = SAMPLESHEET_DIR / f"{name}_{release}_{guardrails.short_random()}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return samplesheet.generate(folder, schemas.samplesheet_columns(s["input"]), s["input"], extra_columns or {}, out_path)



@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@audited
async def validate_inputs(name: str, release: str, params: dict, samplesheet_path: str) -> ValidationResult:
    """Check parameters and samplesheet against the pipeline's own schemas. A pass returns the validation_id that launching requires.

    Leave out `input` and `outdir`: the server sets `input` to samplesheet_path and chooses `outdir` itself.
    """
    s = await pipeline_schemas(name, release)
    if s["input"] is None:
        raise ToolError(f"nf-core/{name} {release} has no samplesheet schema, so there is no samplesheet to validate.")

    sheet = guardrails.check_path(samplesheet_path, [*ALLOWED_DATA_DIRS, RUNS_DIR])
    if not sheet.is_file():
        raise ToolError(f"{sheet} does not exist. Run generate_samplesheet first, or check the path.")

    # The real outdir is set at launch (M3).
    params = {**params, "input": str(sheet), "outdir": str(RUNS_DIR / "pending")}
    errors = validation.validate_params(params, s["params"]) + validation.validate_rows(validation.read_samplesheet(sheet), s["input"])
    if errors:
        return ValidationResult(valid=False, errors=errors, validation_id=None)
    else:
        return ValidationResult(valid=True, errors=[], validation_id=guardrails.issue_validation_id(name, release, params, sheet))


class LaunchResult(TypedDict):
    run_id: str
    results_dir: str
    profiles: list[str]


@mcp.tool()
@audited
async def launch(name: str, release: str, validation_id: str, profile: str = "test,docker,emulate_amd64") -> LaunchResult:
    """Start a pipeline run in the background with inputs that passed validate_inputs, and return its run_id at once.

    profile is a comma-separated list from: test, docker, emulate_amd64, arm64, singularity, conda. A run takes minutes to hours; follow it with run_status.
    """
    entry = guardrails.check_pipeline(name, pipelines())
    guardrails.check_release(entry, release)

    validated = guardrails.check_validation_id(validation_id, name, release)
    profiles = guardrails.check_profiles(profile, ALLOWED_PROFILES)
    if "docker" in profiles and not runs.docker_available():
        raise ToolError("Docker is not available. Run `colima start` to start Docker on this machine.")

    run_id = runs.new_run_id(name)
    record = runs.start(RUNS_DIR / run_id, name, release, profiles, validated["params"])
    return LaunchResult(run_id=run_id, results_dir=record["params"]["outdir"], profiles=profiles)

@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@audited
def run_status(run_id: str) -> runs.RunStatus:
    """The state of a launched run: running, completed, failed or stopped, with task counts, and the error report when it failed."""
    run_dir = guardrails.check_run_id(run_id, RUNS_DIR)
    return runs.status(run_dir)

@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
@audited
def run_summary(run_id: str) -> summary.RunSummary:
    """For a completed run: the output folders, MultiQC's headline numbers per sample, software versions, the parameters set, and the pipeline's own methods text with references, ready to adapt for a paper.

    The methods text's command line does not show `parameters`, which were passed in a params file; mention them alongside it.
    """
    run_dir = guardrails.check_run_id(run_id, RUNS_DIR)
    # Read the state once: a run can finish between two reads, and the message must name the state that failed the check.
    state = runs.status(run_dir)["state"]
    if state != "completed":
        raise ToolError(f"Run {run_id} is {state}; only a completed run can be summarised. Use run_status to see where it stands.")
    return summary.build_summary(run_dir, runs.load_record(run_dir))


@mcp.resource("nfcore://catalog", mime_type="application/json")
@audited
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

"""Fetch a pipeline's two schemas and turn them into something a model can read.

A release has two JSON Schemas (draft 2020-12):

- `nextflow_schema.json`: the parameters. Grouped under `$defs`, pulled in with an `allOf` of `$ref`s; a few pipelines also have top-level `properties`.
- the samplesheet schema, usually `assets/schema_input.json`: an array whose `items` describe one row. The `input` parameter names its path in a `schema` key, so read it from there rather than hard-coding it.

The raw schemas are kept for validation.py; the summaries in here are what goes to the model.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict

import httpx

from nfcore_mcp import catalog

PARAMS_SCHEMA_PATH = "nextflow_schema.json"
DEFAULT_INPUT_SCHEMA_PATH = "assets/schema_input.json"


class ParamInfo(TypedDict):
    name: str
    type: str
    description: str
    enum: list | None


class ColumnInfo(TypedDict):
    name: str
    type: str
    required: bool
    is_file: bool  # format is "file-path": the generator puts reads in these columns
    pattern: str | None
    meta: list[str]  # nf-schema's meta keys; the column with "id" in it holds the sample name
    description: str  # the column's errorMessage, which is the closest thing to a description these schemas have


class PipelineSchemas(TypedDict):
    params: dict  # raw nextflow_schema.json
    input: dict | None  # raw samplesheet schema; None when the pipeline has none


async def load_schema_file(client: httpx.AsyncClient, name: str, release: str, path: str, cache_dir: Path) -> dict | None:
    """Return one schema file of a release as parsed JSON, from cache_dir when present, else from GitHub (and then cache it).

    Return None when the file does not exist at that release.
    """
    cached = cache_dir / name / release / path
    if cached.exists():
        return json.loads(cached.read_text())

    text = await catalog.fetch_pipeline_file(client, name, release, path)
    if text is None:
        return None

    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(text)
    return json.loads(text)


def iter_params(params_schema: dict) -> Iterator[tuple[str, dict, bool]]:
    """Yield (name, spec, required) for every parameter in a nextflow_schema.json, in schema order.

    Walk every `$ref` in `allOf` (they look like "#/$defs/input_output_options"), then the top-level `properties`. A parameter is required when its name is in the `required` list of the group it sits in.
    """
    groups = [params_schema["$defs"][ref["$ref"].split("/")[-1]] for ref in params_schema.get("allOf", [])]
    groups.append(params_schema)  # the top level is a group too
    for group in groups:
        required = set(group.get("required", []))
        for name, spec in group.get("properties", {}).items():
            yield name, spec, name in required


def type_name(spec: dict) -> str:
    """One readable type for a parameter or column, however the schema spells it.

    JSON Schema allows "type": "string", a list "type": ["string", "integer"] (viralrecon's sample column), or no type with alternatives under anyOf (raredisease's sex: integer 0/1/2 or the string "other"). All become one string, "string or integer", because ParamInfo and ColumnInfo promise a str: the SDK checks a tool's result against its declared type and fails the whole call on a mismatch.
    """
    declared = spec.get("type")
    if isinstance(declared, str):
        return declared
    if isinstance(declared, list):
        names = [str(t) for t in declared]
    else:
        names = [type_name(alternative) for alternative in spec.get("anyOf", []) if isinstance(alternative, dict)]
    unique = list(dict.fromkeys(n for n in names if n))  # keep order, drop repeats and blanks
    return " or ".join(unique)


def required_params(params_schema: dict) -> list[ParamInfo]:
    """The parameters a user must set, with type, description and allowed values."""
    return [
        ParamInfo(
            name=name,
            type=type_name(spec),
            description=spec.get("description", ""),
            enum=spec.get("enum"),  # None means any value; [] would mean none
        )
        for name, spec, required in iter_params(params_schema)
        if required
    ]


def input_schema_path(params_schema: dict) -> str | None:
    """The repository path of the samplesheet schema, read from the `input` parameter's `schema` key.

    Return DEFAULT_INPUT_SCHEMA_PATH when `input` exists but names no schema, and None when there is no `input` parameter at all.
    """
    for name, spec, _ in iter_params(params_schema):
        if name == "input":
            return spec.get("schema", DEFAULT_INPUT_SCHEMA_PATH)
    return None


def samplesheet_columns(input_schema: dict) -> list[ColumnInfo]:
    """One ColumnInfo per samplesheet column, in schema order.

    Return [] when the rows are not objects (fetchngs takes a plain list of accession IDs).
    """
    # A list of IDs has items like {"type": "string"}: no properties, so the loop runs zero times and [] comes back.
    items = input_schema.get("items", {})
    required = set(items.get("required", []))
    return [
        ColumnInfo(
            name=name,
            type=type_name(spec),
            required=name in required,
            is_file=spec.get("format") == "file-path",
            pattern=spec.get("pattern"),
            meta=spec.get("meta", []),
            description=spec.get("errorMessage", ""),
        )
        for name, spec in items.get("properties", {}).items()
    ]


async def load_schemas(client: httpx.AsyncClient, name: str, release: str, cache_dir: Path) -> PipelineSchemas:
    """Both schemas of a release. Raise catalog.CatalogError when nextflow_schema.json is missing: every nf-core release has one."""
    params = await load_schema_file(client, name, release, PARAMS_SCHEMA_PATH, cache_dir)
    if params is None:
        raise catalog.CatalogError(f"nf-core/{name} release {release} has no {PARAMS_SCHEMA_PATH}.")
    path = input_schema_path(params)
    input = await load_schema_file(client, name, release, path, cache_dir) if path else None
    return PipelineSchemas(params=params, input=input)

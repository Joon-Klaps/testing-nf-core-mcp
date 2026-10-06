"""Offline tests for M2. They pin down behaviour; run with `uv run pytest tests/test_m2.py`.

The schemas below are trimmed copies of nf-core/demo 1.2.0 (params and samplesheet), plus an rnaseq-style required `strandedness` column.
"""

import asyncio
import copy
import csv
from pathlib import Path

import pytest

from nfcore_mcp import guardrails
from nfcore_mcp.guardrails import Refused, check_path, check_validation_id, issue_validation_id
from nfcore_mcp.samplesheet import generate, pair_reads, read_columns, sample_column
from nfcore_mcp.schemas import input_schema_path, iter_params, load_schema_file, required_params, samplesheet_columns
from nfcore_mcp.validation import read_samplesheet, validate_params, validate_rows

FASTQ_PATTERN = "^([\\S\\s]*\\/)?[^\\s\\/]+\\.f(ast)?q\\.gz$"

PARAMS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "$defs": {
        "input_output_options": {
            "type": "object",
            "required": ["input", "outdir"],
            "properties": {
                "input": {
                    "type": "string",
                    "format": "file-path",
                    "schema": "assets/schema_input.json",
                    "pattern": "^\\S+\\.(csv|tsv|json|yaml|yml)$",
                    "description": "Path to the samplesheet.",
                },
                "outdir": {"type": "string", "format": "directory-path", "description": "The output directory."},
            },
        },
        "process_skipping_options": {
            "type": "object",
            "properties": {"skip_trim": {"type": "boolean", "description": "Skip trimming."}},
        },
    },
    "allOf": [{"$ref": "#/$defs/input_output_options"}, {"$ref": "#/$defs/process_skipping_options"}],
    "properties": {"aligner": {"type": "string", "enum": ["star", "hisat2"], "description": "Aligner."}},
}

INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "sample": {
                "type": "string",
                "pattern": "^\\S+$",
                "errorMessage": "Sample name must be provided and cannot contain spaces",
                "meta": ["id"],
            },
            "fastq_1": {
                "type": "string",
                "format": "file-path",
                "exists": True,
                "pattern": FASTQ_PATTERN,
                "errorMessage": "FastQ file for reads 1 must have extension '.fq.gz' or '.fastq.gz'",
            },
            "fastq_2": {
                "type": "string",
                "format": "file-path",
                "exists": True,
                "pattern": FASTQ_PATTERN,
                "errorMessage": "FastQ file for reads 2 must have extension '.fq.gz' or '.fastq.gz'",
            },
        },
        "required": ["sample", "fastq_1"],
    },
}

# rnaseq-style: one extra column the file names cannot tell
RNASEQ_INPUT_SCHEMA = copy.deepcopy(INPUT_SCHEMA)
RNASEQ_INPUT_SCHEMA["items"]["properties"]["strandedness"] = {
    "type": "string",
    "enum": ["forward", "reverse", "unstranded", "auto"],
    "errorMessage": "Strandedness must be one of forward, reverse, unstranded or auto",
}
RNASEQ_INPUT_SCHEMA["items"]["required"].append("strandedness")


def touch(folder: Path, *names: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / n for n in names]
    for p in paths:
        p.write_bytes(b"")
    return paths


# --- schemas.py ---


def test_iter_params_walks_defs_then_top_level():
    params = list(iter_params(PARAMS_SCHEMA))
    assert [name for name, _, _ in params] == ["input", "outdir", "skip_trim", "aligner"]
    assert {name for name, _, required in params if required} == {"input", "outdir"}


def test_required_params_carry_type_and_description():
    by_name = {p["name"]: p for p in required_params(PARAMS_SCHEMA)}
    assert set(by_name) == {"input", "outdir"}
    assert by_name["outdir"]["type"] == "string"
    assert by_name["outdir"]["description"] == "The output directory."


def test_input_schema_path_comes_from_the_input_param():
    assert input_schema_path(PARAMS_SCHEMA) == "assets/schema_input.json"
    no_input = {"$defs": {}, "allOf": [], "properties": {"outdir": {"type": "string"}}}
    assert input_schema_path(no_input) is None


def test_samplesheet_columns_in_schema_order_with_flags():
    columns = samplesheet_columns(INPUT_SCHEMA)
    assert [c["name"] for c in columns] == ["sample", "fastq_1", "fastq_2"]
    assert [c["required"] for c in columns] == [True, True, False]
    assert [c["is_file"] for c in columns] == [False, True, True]
    assert columns[0]["meta"] == ["id"]
    assert columns[1]["pattern"] == FASTQ_PATTERN


def test_samplesheet_columns_empty_for_a_list_of_ids():
    assert samplesheet_columns({"type": "array", "items": {"type": "string"}}) == []


def test_load_schema_file_reads_the_cache_without_network(tmp_path):
    cached = tmp_path / "demo" / "1.2.0" / "assets" / "schema_input.json"
    cached.parent.mkdir(parents=True)
    cached.write_text('{"cached": true}')
    # client=None: touching the network would fail, so this passes only if the cache is used
    assert asyncio.run(load_schema_file(None, "demo", "1.2.0", "assets/schema_input.json", tmp_path)) == {"cached": True}


# --- validation.py ---


def test_validate_params_passes_a_good_set():
    assert validate_params({"input": "s.csv", "outdir": "results"}, PARAMS_SCHEMA) == []


def test_validate_params_names_the_missing_required_param():
    errors = validate_params({"input": "s.csv"}, PARAMS_SCHEMA)
    assert len(errors) == 1
    assert "outdir" in errors[0]


def test_validate_params_catches_wrong_enum_and_unknown_params():
    errors = validate_params({"input": "s.csv", "outdir": "r", "aligner": "bwa", "made_up": 1}, PARAMS_SCHEMA)
    assert any("aligner" in e for e in errors)
    assert any("made_up" in e for e in errors)


def test_read_samplesheet_drops_empty_cells(tmp_path):
    path = tmp_path / "s.csv"
    path.write_text("sample,fastq_1,fastq_2\nA,a_1.fastq.gz,\n")
    assert read_samplesheet(path) == [{"sample": "A", "fastq_1": "a_1.fastq.gz"}]


def test_validate_rows_passes_good_rows(tmp_path):
    r1, r2 = touch(tmp_path, "A_R1.fastq.gz", "A_R2.fastq.gz")
    assert validate_rows([{"sample": "A", "fastq_1": str(r1), "fastq_2": str(r2)}], INPUT_SCHEMA) == []


def test_validate_rows_uses_the_schema_error_message_and_row_number(tmp_path):
    (r1,) = touch(tmp_path, "A_R1.fastq.gz")
    rows = [{"sample": "A", "fastq_1": str(r1)}, {"sample": "has space", "fastq_1": str(r1)}]
    errors = validate_rows(rows, INPUT_SCHEMA)
    assert len(errors) == 1
    assert "Row 2" in errors[0]
    assert "cannot contain spaces" in errors[0]


def test_validate_rows_reports_a_missing_required_column():
    errors = validate_rows([{"sample": "A"}], INPUT_SCHEMA)
    assert len(errors) == 1
    assert "Row 1" in errors[0] and "fastq_1" in errors[0]


def test_validate_rows_checks_files_exist(tmp_path):
    errors = validate_rows([{"sample": "A", "fastq_1": str(tmp_path / "gone_R1.fastq.gz")}], INPUT_SCHEMA)
    assert len(errors) == 1
    assert "does not exist" in errors[0]


# --- samplesheet.py ---


def test_pair_reads_handles_common_naming_schemes(tmp_path):
    files = touch(
        tmp_path,
        "SAMPLE1_R1.fastq.gz",
        "SAMPLE1_R2.fastq.gz",
        "s2_1.fq.gz",
        "s2_2.fq.gz",
        "S3_S3_L001_R1_001.fastq.gz",
        "S3_S3_L001_R2_001.fastq.gz",
        "single.fastq.gz",
    )
    read_sets, unmatched = pair_reads(files)
    assert unmatched == []
    by_sample = {rs["sample"]: rs for rs in read_sets}
    assert set(by_sample) == {"SAMPLE1", "s2", "S3", "single"}
    assert by_sample["SAMPLE1"]["read2"].name == "SAMPLE1_R2.fastq.gz"
    assert by_sample["S3"]["read1"].name == "S3_S3_L001_R1_001.fastq.gz"
    assert by_sample["single"]["read2"] is None


def test_pair_reads_reports_a_read2_without_read1(tmp_path):
    files = touch(tmp_path, "orphan_R2.fastq.gz")
    read_sets, unmatched = pair_reads(files)
    assert read_sets == []
    assert unmatched == ["orphan_R2.fastq.gz"]


def test_sample_and_read_columns_come_from_the_schema():
    # Renamed columns, as in mag (short_reads_1/2) or ampliseq (forwardReads/reverseReads)
    renamed = copy.deepcopy(INPUT_SCHEMA)
    props = renamed["items"]["properties"]
    renamed["items"]["properties"] = {"id": props["sample"], "reads_fwd": props["fastq_1"], "reads_rev": props["fastq_2"]}
    columns = samplesheet_columns(renamed)
    assert sample_column(columns) == "id"
    assert read_columns(columns, Path("/data/A_R1.fastq.gz")) == ["reads_fwd", "reads_rev"]


def test_generate_writes_a_valid_samplesheet(tmp_path):
    touch(tmp_path / "reads", "A_R1.fastq.gz", "A_R2.fastq.gz", "B_R1.fastq.gz", "B_R2.fastq.gz")
    out = tmp_path / "out" / "samplesheet.csv"
    out.parent.mkdir()
    report = generate(tmp_path / "reads", samplesheet_columns(INPUT_SCHEMA), INPUT_SCHEMA, {}, out)
    assert report["errors"] == [] and report["missing_required"] == []
    assert report["path"] == str(out)
    assert report["samples"] == 2
    with open(out) as f:
        rows = list(csv.DictReader(f))
    assert [r["sample"] for r in rows] == ["A", "B"]
    assert Path(rows[0]["fastq_1"]).is_absolute()


def test_generate_reports_a_missing_required_column_and_writes_nothing(tmp_path):
    touch(tmp_path / "reads", "A_R1.fastq.gz", "A_R2.fastq.gz")
    out = tmp_path / "samplesheet.csv"
    report = generate(tmp_path / "reads", samplesheet_columns(RNASEQ_INPUT_SCHEMA), RNASEQ_INPUT_SCHEMA, {}, out)
    assert "strandedness" in report["missing_required"]
    assert report["path"] is None
    assert not out.exists()


def test_generate_fills_extra_columns(tmp_path):
    touch(tmp_path / "reads", "A_R1.fastq.gz", "A_R2.fastq.gz")
    out = tmp_path / "samplesheet.csv"
    columns = samplesheet_columns(RNASEQ_INPUT_SCHEMA)
    report = generate(tmp_path / "reads", columns, RNASEQ_INPUT_SCHEMA, {"strandedness": "auto"}, out)
    assert report["path"] == str(out)
    assert read_samplesheet(out)[0]["strandedness"] == "auto"


def test_generate_rejects_unknown_extra_columns(tmp_path):
    touch(tmp_path / "reads", "A_R1.fastq.gz")
    out = tmp_path / "samplesheet.csv"
    report = generate(tmp_path / "reads", samplesheet_columns(INPUT_SCHEMA), INPUT_SCHEMA, {"strandness": "auto"}, out)
    assert any("strandness" in e for e in report["errors"])
    assert report["path"] is None


# --- guardrails.py ---


def test_check_path_allows_inside_and_refuses_escapes(tmp_path):
    allowed = tmp_path / "data"
    allowed.mkdir()
    assert check_path(str(allowed / "reads"), [allowed]) == (allowed / "reads").resolve()
    for outside in [str(tmp_path), str(allowed / ".." / "secrets"), "/etc"]:
        with pytest.raises(Refused):
            check_path(outside, [allowed])


def test_check_path_follows_symlinks(tmp_path):
    allowed = tmp_path / "data"
    allowed.mkdir()
    (allowed / "sneaky").symlink_to(tmp_path)
    with pytest.raises(Refused):
        check_path(str(allowed / "sneaky"), [allowed])


@pytest.fixture
def clean_validations():
    guardrails._validations.clear()
    yield
    guardrails._validations.clear()


def test_validation_id_round_trip(tmp_path, clean_validations):
    sheet = tmp_path / "s.csv"
    sheet.write_text("sample,fastq_1\n")
    vid = issue_validation_id("demo", "1.2.0", {"outdir": "r"}, sheet)
    assert check_validation_id(vid, "demo", "1.2.0")["params"] == {"outdir": "r"}


def test_validation_id_refused_when_unknown_or_for_another_pipeline(tmp_path, clean_validations):
    sheet = tmp_path / "s.csv"
    sheet.write_text("sample,fastq_1\n")
    vid = issue_validation_id("demo", "1.2.0", {}, sheet)
    for args in [("made-up-id", "demo", "1.2.0"), (vid, "rnaseq", "1.2.0"), (vid, "demo", "1.1.0")]:
        with pytest.raises(Refused):
            check_validation_id(*args)


def test_validation_id_refused_when_samplesheet_changed(tmp_path, clean_validations):
    sheet = tmp_path / "s.csv"
    sheet.write_text("sample,fastq_1\n")
    vid = issue_validation_id("demo", "1.2.0", {}, sheet)
    sheet.write_text("sample,fastq_1\nsneaky,/etc/passwd\n")
    with pytest.raises(Refused):
        check_validation_id(vid, "demo", "1.2.0")



@pytest.mark.parametrize(
    "names",
    [
        ["A.fastq.gz", "A_1.fastq.gz", "A_2.fastq.gz"],  # single-end next to a pair
        ["A_R1.fastq.gz", "A_1.fastq.gz", "A_R2.fastq.gz"],  # two files for read 1
        ["A_R1.fq.gz", "A_R1.fastq.gz"],  # the same read twice with different extensions
    ],
)
def test_pair_reads_reports_ambiguous_prefixes_whole(tmp_path, names):
    read_sets, unmatched = pair_reads(touch(tmp_path, *names))
    assert read_sets == []
    assert sorted(unmatched) == sorted(names)


@pytest.mark.parametrize(
    "spec, expected",
    [
        ({"type": "string"}, "string"),
        ({"type": ["string", "integer"]}, "string or integer"),  # viralrecon's sample column
        ({"anyOf": [{"type": "integer", "enum": [0, 1, 2]}, {"type": "string", "enum": ["other"]}]}, "integer or string"),  # raredisease's sex
        ({"anyOf": [{"type": "string"}, {"maxLength": 0}]}, "string"),  # raredisease's paternal_id: the untyped branch adds nothing
        ({}, ""),
    ],
)
def test_type_name_handles_every_spelling(spec, expected):
    from nfcore_mcp.schemas import type_name

    assert type_name(spec) == expected


def test_columns_pass_the_sdk_output_check_with_a_list_type():
    # The SDK validates a tool's result against its declared type; a list in a str field failed get_pipeline_schema for viralrecon.
    from pydantic import TypeAdapter

    from nfcore_mcp.schemas import ColumnInfo

    schema = copy.deepcopy(INPUT_SCHEMA)
    schema["items"]["properties"]["sample"]["type"] = ["string", "integer"]
    columns = samplesheet_columns(schema)
    TypeAdapter(list[ColumnInfo]).validate_python(columns, strict=True)
    assert columns[0]["type"] == "string or integer"

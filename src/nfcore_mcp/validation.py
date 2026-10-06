"""Check parameters and samplesheet rows against a pipeline's own schemas, and say what is wrong in plain sentences.

No MCP, no HTTP, no files written: schemas and data in, a list of sentences out. An empty list means valid.

Why not `nf-core pipelines schema validate`: it needs a local checkout of the pipeline (`--dir`), while the schemas are already cached as JSON. jsonschema does the same check in-process, and it is testable offline. The trade-off: nf-schema's own keywords (`exists`, `errorMessage`, `format: file-path`) are unknown to jsonschema, which ignores them, so the two that matter are handled here by hand.
"""

import csv
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from nfcore_mcp.schemas import iter_params


def describe_error(error: ValidationError, where: str, error_messages: dict[str, str]) -> str:
    """Turn one jsonschema error into a sentence such as "Row 2, column sample: Sample name cannot contain spaces".

    where is the prefix ("Row 2" or "Parameters"). error_messages maps a field name to the schema's own `errorMessage`, which is written for humans and beats jsonschema's message when there is one.
    The field is error.path[0] when the value was wrong. For a missing field (error.validator == "required") the path is empty; the field name is in the message, and error.validator_value holds the required list.
    """
    if error.validator == "required" and isinstance(error.instance, dict) and isinstance(error.validator_value, list):
        missing = [field for field in error.validator_value if field not in error.instance]
        if missing:
            return f"{where}: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} required but missing."

    if not error.path:
        return f"{where}: {error.message}"

    field = str(error.path[0])
    message = error_messages.get(field) or error.message
    return f"{where}, {field}: {message}"

def validate_params(params: dict, params_schema: dict) -> list[str]:
    """Check params against nextflow_schema.json. Also flag parameters the schema does not know, which is how a model's invented parameter gets caught."""
    known = {name for name, _, _ in iter_params(params_schema)}
    unknown_params = [f"Parameters: {name} is not a known parameter." for name in params if name not in known]
    validator = Draft202012Validator(params_schema)
    error_messages = {name: spec.get("errorMessage", "") for name, spec, _ in iter_params(params_schema)}
    validation_errors = [describe_error(error, "Parameters", error_messages) for error in validator.iter_errors(params)]
    return unknown_params + validation_errors



def read_samplesheet(path: Path) -> list[dict[str, str]]:
    """Read a samplesheet CSV into one dict per row, dropping empty cells.

    nf-schema treats an empty cell as absent, so a single-end row has no fastq_2 key at all rather than fastq_2 = "".
    """
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        return [{k: v for k, v in row.items() if v} for row in reader]

def validate_rows(rows: list[dict[str, str]], input_schema: dict) -> list[str]:
    """Check every row against the samplesheet schema. Rows are numbered from 1, the way a researcher counts them below the header.

    On top of jsonschema: a column with `"exists": true` must name a file that exists on disk.
    """
    row_schema = input_schema["items"]
    properties = row_schema.get("properties", {})
    error_messages = {col: spec["errorMessage"] for col, spec in properties.items() if "errorMessage" in spec}
    file_columns = [col for col, spec in properties.items() if spec.get("exists")]

    validator = Draft202012Validator(row_schema)

    errors: list[str] = []
    for i, row in enumerate(rows, start=1):
        where = f"Row {i}"
        schema_errors = sorted(validator.iter_errors(row), key=lambda e: [str(p) for p in e.path])
        errors.extend(describe_error(error, where, error_messages) for error in schema_errors)

        for col in file_columns:
            if row.get(col) and not Path(row[col]).exists():
                errors.append(f"{where}, {col}: file {row[col]} does not exist.")

    return errors

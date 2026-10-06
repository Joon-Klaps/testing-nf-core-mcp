"""The rules the server enforces, whatever client or model is connected.

Every check raises Refused with a sentence the model can read and act on. M2 and M3 add their checks here (validation ids, profile allow list, allowed paths).
"""

import hashlib
import secrets
from pathlib import Path
from typing import TypedDict

from mcp.server.mcpserver.exceptions import ToolError

from nfcore_mcp.index import IndexEntry
from nfcore_mcp.runs import RUN_ID


class Refused(ToolError):
    """A request the server will not carry out. The SDK returns the message to the client as a tool error."""


def check_pipeline(name: str, entries: list[IndexEntry]) -> IndexEntry:
    """Return the index entry for name, or refuse: only indexed nf-core pipelines, never an arbitrary repository."""
    for entry in entries:
        if entry["name"] == name:
            return entry
    raise Refused(f"nf-core/{name} is not in the index. Use search_pipelines to find a pipeline.")


def check_release(entry: IndexEntry, release: str) -> None:
    """Refuse `dev` and any release other than the indexed one. Pinned releases only."""
    if release != entry["release"]:
        raise Refused(
            f'nf-core/{entry["name"]} is only available at release {entry["release"]}, not {release!r}. Retry with release="{entry["release"]}".'
        )


def check_path(path: str, allowed: list[Path]) -> Path:
    """Return path resolved to an absolute path, or refuse when it does not sit inside one of the allowed directories.

    Resolve first: "data/../../.ssh" and symlinks must be judged by where they really point, not by how they are spelled.
    """
    resolved = Path(path).expanduser().resolve()
    if any(resolved.is_relative_to(root) for root in allowed):
        return resolved
    allowed_list = ", ".join(str(root) for root in allowed)
    raise Refused(f"{path} is outside the directories this server may read: {allowed_list}. Ask the user to move the data there.")


class ValidatedInputs(TypedDict):
    name: str
    release: str
    params: dict
    samplesheet: str
    samplesheet_sha256: str


# validation_id -> what was validated. In memory: a restarted server forgets them and the model validates again, which is cheap and errs on the safe side.
_validations: dict[str, ValidatedInputs] = {}


def file_sha256(path: Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()

def short_random() -> str:
    """Return a short random string for a temporary file name. Not a security token, just to avoid collisions."""
    return secrets.token_hex(4)

def issue_validation_id(name: str, release: str, params: dict, samplesheet: Path) -> str:
    """Record inputs that passed validate_inputs and return a fresh id for them. M3's launch accepts nothing else."""
    validation_id = secrets.token_hex(8)  # random, so the model cannot guess or construct one
    _validations[validation_id] = ValidatedInputs(
        name=name,
        release=release,
        params=params,
        samplesheet=str(samplesheet),
        samplesheet_sha256=file_sha256(samplesheet),
    )
    return validation_id


def check_validation_id(validation_id: str, name: str, release: str) -> ValidatedInputs:
    """Return what was validated under validation_id, or refuse when the id is unknown, belongs to another pipeline or release, or the samplesheet changed on disk since."""
    record = _validations.get(validation_id)
    if record is None:
        raise Refused(f"Unknown validation_id {validation_id!r}. Run validate_inputs first.")
    if (record["name"], record["release"]) != (name, release):
        raise Refused(
            f"validation_id {validation_id!r} was issued for nf-core/{record['name']} {record['release']}, not nf-core/{name} {release}. Run validate_inputs for this pipeline."
        )
    sheet = Path(record["samplesheet"])
    if not sheet.exists() or file_sha256(sheet) != record["samplesheet_sha256"]:
        raise Refused(f"{sheet} changed or disappeared after validation. Run validate_inputs again.")
    return record


def check_profiles(profile: str, allowed: set[str]) -> list[str]:
    """Split a comma-separated profile string and refuse any profile not in allowed. A profile can point Nextflow at any config, so only known ones pass."""
    profiles = [p.strip() for p in profile.split(",") if p.strip()]
    if not profiles:
        raise Refused(f"No profile given. Choose from: {', '.join(sorted(allowed))}.")
    rejected = [p for p in profiles if p not in allowed]
    if rejected:
        raise Refused(f"Profile {', '.join(rejected)} is not allowed. Choose from: {', '.join(sorted(allowed))}.")
    return profiles


def check_run_id(run_id: str, runs_dir: Path) -> Path:
    """Return the folder of an existing run, or refuse. The id must match the server's own format, so "../" or an absolute path never becomes a folder."""
    if not RUN_ID.match(run_id) or not (runs_dir / run_id / "run.json").is_file():
        raise Refused(f"No run with id {run_id!r}. Use the run_id that launch returned.")
    return runs_dir / run_id

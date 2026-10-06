"""Turn a folder of FASTQ files into a samplesheet for a given pipeline.

Which column takes what comes from the samplesheet schema, never from the pipeline's name: rnaseq, mag and ampliseq name their read columns differently. The generator fills what it can derive from file names and the caller's extra_columns, and reports the rest. It never invents a value.
"""

import csv
import re
from pathlib import Path
from typing import TypedDict

from nfcore_mcp.schemas import ColumnInfo
from nfcore_mcp.validation import validate_rows

# One FASTQ file name: the prefix shared by both mates, then an optional read marker (_1, _2, _R1, _R2) and Illumina's _001.
# "S1_S1_L001_R1_001.fastq.gz" -> prefix "S1_S1_L001", read "1"; "sampleB.fastq.gz" -> prefix "sampleB", read None (single-end).
READ_FILE = re.compile(r"^(?P<prefix>.+?)(?:_R?(?P<read>[12]))?(?:_001)?\.f(?:ast)?q\.gz$")

# Illumina's sample number and lane at the end of a prefix: "S1_S1_L001" -> "S1". Lanes of one sample become separate rows with the same sample name, which nf-core pipelines merge.
ILLUMINA_SUFFIX = re.compile(r"_S\d+(?:_L\d{3})?$")


class ReadSet(TypedDict):
    sample: str
    read1: Path
    read2: Path | None


class SamplesheetReport(TypedDict):
    path: str | None  # None when nothing was written, because of errors
    samples: int
    columns: list[str]
    missing_required: list[str]  # required columns neither the files nor extra_columns filled
    unmatched_files: list[str]  # files in input_dir that READ_FILE could not place
    errors: list[str]  # from validate_rows


def pair_reads(files: list[Path]) -> tuple[list[ReadSet], list[str]]:
    """Group FASTQ files into read sets by prefix. Return the read sets sorted by sample, and the names of files that could not be placed.

    A file is unplaceable when READ_FILE does not match it, when a prefix has a read 2 but no read 1, or when a prefix is ambiguous: two files for the same read (A_R1.fastq.gz and A_1.fastq.gz), or a single-end file next to paired ones (A.fastq.gz and A_1.fastq.gz). An ambiguous prefix is reported whole rather than resolved by guessing.

    A single-end file whose name ends in _1 or _2 ("virus_1.fastq.gz") reads as read 1 of sample "virus"; the file name alone cannot tell it apart.
    """
    by_prefix: dict[str, dict[str | None, list[Path]]] = {}
    unmatched: list[str] = []
    for f in files:
        m = READ_FILE.match(f.name)
        if m:
            by_prefix.setdefault(m["prefix"], {}).setdefault(m["read"], []).append(f)
        else:
            unmatched.append(f.name)

    read_sets: list[ReadSet] = []
    for prefix, reads in by_prefix.items():
        all_files = [f.name for paths in reads.values() for f in paths]
        duplicate_read = any(len(paths) > 1 for paths in reads.values())
        single_next_to_paired = None in reads and ("1" in reads or "2" in reads)
        read1 = reads.get("1") or reads.get(None)
        if duplicate_read or single_next_to_paired or read1 is None:
            unmatched.extend(all_files)
            continue
        read2 = reads.get("2")
        read_sets.append(
            ReadSet(sample=ILLUMINA_SUFFIX.sub("", prefix), read1=read1[0], read2=read2[0] if read2 else None)
        )
    read_sets.sort(key=lambda rs: (rs["sample"], rs["read1"].name))
    return read_sets, unmatched


def sample_column(columns: list[ColumnInfo]) -> str | None:
    """The column that takes the sample name: the one whose meta contains "id", else one called "sample", else None."""
    for c in columns:
        if "id" in c["meta"]:
            return c["name"]
    return "sample" if any(c["name"] == "sample" for c in columns) else None


def read_columns(columns: list[ColumnInfo], example: Path) -> list[str]:
    """The first two file columns whose pattern matches example (a FASTQ path), in schema order: [read 1 column, read 2 column].

    Patterns are written for full paths, so match against str(example) with re.search. A file column without a pattern matches anything.
    """
    matching = [
        c["name"] for c in columns if c["is_file"] and (c["pattern"] is None or re.search(c["pattern"], str(example)))
    ]
    return matching[:2]


def build_rows(read_sets: list[ReadSet], columns: list[ColumnInfo], extra_columns: dict[str, str]) -> list[dict[str, str]]:
    """One row per read set: sample name, absolute paths of the reads, then extra_columns (same value on every row).

    A schema without a sample column or without a column that accepts FASTQ files gets rows without those values; missing_required and validate_rows then say what is wrong.
    """
    if not read_sets:
        return []
    id_col = sample_column(columns)
    file_cols = read_columns(columns, read_sets[0]["read1"])

    rows = []
    for rs in read_sets:
        row: dict[str, str] = {}
        if id_col:
            row[id_col] = rs["sample"]
        if file_cols:
            row[file_cols[0]] = str(rs["read1"].resolve())
        if rs["read2"] and len(file_cols) > 1:
            row[file_cols[1]] = str(rs["read2"].resolve())
        row.update(extra_columns)
        rows.append(row)
    return rows


def missing_required(rows: list[dict[str, str]], columns: list[ColumnInfo]) -> list[str]:
    """Required columns that are empty on at least one row."""
    return [c["name"] for c in columns if c["required"] and any(not row.get(c["name"]) for row in rows)]


def write_samplesheet(rows: list[dict[str, str]], columns: list[ColumnInfo], path: Path) -> None:
    """Write rows as CSV with every schema column as header, in schema order, empty cells where a row has no value.

    Writing all columns, not just the filled ones, shows the researcher which optional columns exist.
    """
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[c["name"] for c in columns], restval="")
        writer.writeheader()
        writer.writerows(rows)


def generate(input_dir: Path, columns: list[ColumnInfo], input_schema: dict, extra_columns: dict[str, str], out_path: Path) -> SamplesheetReport:
    """Scan input_dir (not recursive), build and validate the rows, and write out_path only when there is nothing to fix.

    Unknown keys in extra_columns are an error, not silently dropped: a typo like "strandness" should come back to the model.
    """
    names = [c["name"] for c in columns]
    errors = [
        f"extra_columns: {key} is not a column of this samplesheet; the columns are {', '.join(names)}."
        for key in extra_columns
        if key not in names
    ]

    read_sets, unmatched = pair_reads(sorted(input_dir.glob("*.f*q.gz")))
    if not read_sets:
        errors.append(f"No FASTQ files (.fastq.gz or .fq.gz) with a read 1 found in {input_dir}.")

    rows = build_rows(read_sets, columns, extra_columns)
    missing = missing_required(rows, columns)
    # Row errors for the missing columns would only repeat `missing`, so validate rows once everything required is there.
    if rows and not missing:
        errors.extend(validate_rows(rows, input_schema))

    written = not errors and not missing
    if written:
        write_samplesheet(rows, columns, out_path)
    return SamplesheetReport(
        path=str(out_path) if written else None,
        samples=len(rows),
        columns=names,
        missing_required=missing,
        unmatched_files=unmatched,
        errors=errors,
    )

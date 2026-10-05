"""Offline tests for M1. They pin down behaviour; run with `uv run pytest`."""

import pytest

from nfcore_mcp.catalog import latest_release
from nfcore_mcp.guardrails import Refused, check_pipeline, check_release
from nfcore_mcp.index import IndexEntry, read_shortlist
from nfcore_mcp.search import PipelineSearch, matching_lines, tokenize


def entry(name: str, description: str, readme: str = "", topics: list[str] | None = None) -> IndexEntry:
    return IndexEntry(name=name, release="1.0.0", description=description, topics=topics or [], readme=readme)


@pytest.fixture
def entries() -> list[IndexEntry]:
    return [
        entry(
            "viralrecon",
            "Assembly and intrahost variant calling for viral samples",
            "Consensus genomes from viral amplicon reads.",
        ),
        entry(
            "taxprofiler",
            "Taxonomic profiling of shotgun metagenomic data",
            "Classify reads with Kraken2 and MetaPhlAn.",
        ),
        entry("rnaseq", "RNA sequencing analysis pipeline", "Gene expression quantification from RNA-seq reads."),
    ]


def test_read_shortlist_skips_comments_and_blanks(tmp_path):
    path = tmp_path / "shortlist.txt"
    path.write_text("# a comment\n\nmag\n  taxprofiler  \n# rnaseq\n")
    assert read_shortlist(path) == ["mag", "taxprofiler"]


def test_latest_release_ignores_dev_and_list_order():
    pipeline = {
        "releases": [
            {"tag_name": "1.0.0", "published_at": "2024-01-01T00:00:00Z"},
            {"tag_name": "dev", "published_at": "2026-10-01T00:00:00Z"},
            {"tag_name": "1.1.0", "published_at": "2025-01-01T00:00:00Z"},
        ]
    }
    assert latest_release(pipeline) == "1.1.0"


def test_latest_release_none_when_only_dev():
    assert latest_release({"releases": [{"tag_name": "dev", "published_at": "2026-01-01T00:00:00Z"}]}) is None


def test_tokenize_lowercases_and_splits():
    assert tokenize("Paired-end Illumina, RNA-seq!") == ["paired", "end", "illumina", "rna", "seq"]


def test_search_ranks_the_obvious_match_first(entries):
    hits = PipelineSearch(entries).search("consensus genomes from a viral outbreak", k=2)
    assert hits[0]["name"] == "viralrecon"
    assert len(hits) == 2
    assert isinstance(hits[0]["score"], float)


def test_matching_lines_returns_lines_sharing_query_tokens():
    readme = "# Title\n\nCalls variants.\nBuilds consensus genomes for viruses.\n"
    assert matching_lines(readme, {"consensus": 1.0, "genomes": 1.0}) == ["Builds consensus genomes for viruses."]


def test_matching_lines_prefers_rare_words_over_common_ones():
    readme = "It is built with Nextflow and a container and a test.\nViral consensus genomes.\n"
    weights = {"and": 0.01, "a": 0.01, "viral": 2.0}
    assert matching_lines(readme, weights, max_lines=1) == ["Viral consensus genomes."]


def test_search_clamps_k(entries):
    search = PipelineSearch(entries)
    assert len(search.search("viral", k=-1)) == 1
    assert len(search.search("viral", k=1000)) == len(entries)


def test_check_pipeline_refuses_unknown_names(entries):
    assert check_pipeline("taxprofiler", entries)["name"] == "taxprofiler"
    with pytest.raises(Refused):
        check_pipeline("not-a-pipeline", entries)


def test_check_release_refuses_dev_and_other_tags(entries):
    check_release(entries[0], "1.0.0")
    for release in ["dev", "0.9.0"]:
        with pytest.raises(Refused):
            check_release(entries[0], release)

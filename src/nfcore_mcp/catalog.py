"""Talk to nf-core: the pipeline catalog on nf-co.re and pipeline files on GitHub raw.

The only module that does HTTP. Everything else gets plain Python data from here.
"""

import httpx

CATALOG_URL = "https://nf-co.re/pipelines.json"
RAW_URL = "https://raw.githubusercontent.com/nf-core/{name}/{release}/{path}"


def fetch_catalog(client: httpx.Client) -> list[dict]:
    """Download the nf-core catalog and return its `remote_workflows` list, one dict per pipeline."""
    # TODO: client.get(CATALOG_URL), raise_for_status(), .json()["remote_workflows"]
    raise NotImplementedError


def latest_release(pipeline: dict) -> str | None:
    """Return the tag of the newest real release of a catalog entry, or None when it only has `dev`.

    Pick by `published_at`, not by list position: the catalog happens to be newest first today, but nothing promises that.
    """
    # TODO: filter out tag_name == "dev", then max(..., key=lambda r: r["published_at"])
    raise NotImplementedError


def fetch_pipeline_file(client: httpx.Client, name: str, release: str, path: str) -> str | None:
    """Fetch one file of a pipeline at a pinned release, for example README.md or docs/usage.md. Return None when the file does not exist (HTTP 404)."""
    # TODO: format RAW_URL, client.get, return None on 404, raise_for_status otherwise, return .text
    raise NotImplementedError

"""Talk to nf-core: the pipeline catalog on nf-co.re and pipeline files on GitHub raw.

The only module that does HTTP. Everything else gets plain Python data from here.
"""

import httpx

CATALOG_URL = "https://nf-co.re/pipelines.json"
RAW_URL = "https://raw.githubusercontent.com/nf-core/{name}/{release}/{path}"


class CatalogError(RuntimeError):
    """nf-core or GitHub could not be reached, or answered with something unexpected."""


async def fetch_catalog(client: httpx.AsyncClient) -> dict[str, dict]:
    """Download the nf-core catalog and return its pipelines keyed by name."""
    try:
        response = await client.get(CATALOG_URL)
        workflows_list = response.raise_for_status().json()["remote_workflows"]
    except httpx.HTTPError as e:
        raise CatalogError(f"Failed to fetch catalog from {CATALOG_URL}: {e}") from e

    try:
        workflows_dict = {workflow["name"]: workflow for workflow in workflows_list}
    except KeyError as e:
        raise CatalogError("Unexpected catalog format: missing 'name' key in one of the workflows") from e

    return workflows_dict


def latest_release(pipeline: dict) -> str | None:
    """Return the tag of the newest real release of a catalog entry, or None when it only has `dev`.

    Pick by `published_at`, not by list position: the catalog happens to be newest first today, but nothing promises that.
    """
    releases = [r for r in pipeline.get("releases", []) if r.get("tag_name") != "dev"]
    releases.sort(key=lambda r: r.get("published_at", ""), reverse=True)
    return releases[0].get("tag_name") if releases else None


async def fetch_pipeline_file(client: httpx.AsyncClient, name: str, release: str, path: str) -> str | None:
    """Fetch one file of a pipeline at a pinned release, for example README.md or docs/usage.md. Return None when the file does not exist (HTTP 404)."""
    url = RAW_URL.format(name=name, release=release, path=path)
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise CatalogError(f"Failed to fetch {url}: {e}") from e
    except httpx.RequestError as e:
        # Offline, DNS failure, timeout: no HTTP response at all.
        raise CatalogError(f"Could not reach {url}: {e}") from e
    return response.text

"""Thin async wrapper around Cognee Cloud's REST API. Agents never call Cognee directly.

Dataset architecture — every dataset is scoped to a single pipeline run:
- One dataset per sector:  "stocksage_run<run_id>_sector_<slug>"
- One dataset per company: "stocksage_run<run_id>_company_<TICKER>"

Every write is JSON text added via /api/v1/add_text (confirmed empirically:
/api/v1/add only accepts multipart/form-data file uploads and 409s on JSON;
/api/v1/add_text takes a JSON body and is the right primitive for writing
structured dict data), tagged with a node_set so reads can filter precisely
with search's node_name parameter instead of relying on semantic ranking.
Reads use search_type=CHUNKS, which returns the original chunk text verbatim
(GRAPH_COMPLETION paraphrases through an LLM and is not safe for exact
round-tripping of structured data).

There is no dedicated "improve" endpoint on this API surface -- improve_memory()
re-runs cognify across all of a run's StockSage datasets, which is the closest
real primitive to "improve the graph" (re-processing/enrichment of entities and
relationships as more data has accumulated).

Run isolation: each full pipeline execution (one user request -> one PDF) gets
its own run_id, passed as the first argument to every function here. Datasets
from different runs never collide and never get blended together on read.
cleanup_run(run_id) tears down every dataset belonging to a run once it's
done -- this is the primary teardown call.
"""

import asyncio
import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

_write_locks: dict[str, asyncio.Lock] = {}


def _write_lock(dataset_name: str) -> asyncio.Lock:
    # Layer 2's three agents (fundamental/market_signal/risk) write to the
    # SAME per-company dataset concurrently via asyncio.gather. Concurrent
    # add_text+cognify calls against one dataset can race -- observed live:
    # some companies' findings silently never showed up in read_all_findings
    # even though every individual write call succeeded with no error. This
    # serializes writes per dataset while leaving different datasets (e.g.
    # different tickers) fully parallel.
    lock = _write_locks.get(dataset_name)
    if lock is None:
        lock = asyncio.Lock()
        _write_locks[dataset_name] = lock
    return lock


_COGNEE_TIMEOUT_SECONDS = 120  # cognify on non-trivial content can legitimately take a while
_COGNEE_MAX_RETRIES = 2
_COGNEE_RETRY_DELAY_SECONDS = 2
_COGNEE_RETRYABLE_STATUS_CODES = {502, 503, 504}


def _client() -> httpx.AsyncClient:
    base_url = os.environ["COGNEE_BASE_URL"].rstrip("/")
    headers = {"X-Api-Key": os.environ["COGNEE_API_KEY"], "Content-Type": "application/json"}
    return httpx.AsyncClient(base_url=base_url, headers=headers, timeout=_COGNEE_TIMEOUT_SECONDS)


async def _request(method: str, path: str, **kwargs) -> httpx.Response:
    # Retries on transient errors (timeout, connection error, 502/503/504) --
    # same pattern as fetchers.py's USASpending retry and llm_utils.py's
    # Anthropic retry. Cognee is demonstrably the flakiest external
    # dependency in this project (dataset-list lag, search-index lag, and
    # now a live ReadTimeout on cognify); every other HTTP client here
    # already retries transient failures, this one hadn't.
    last_exc = None
    for attempt in range(_COGNEE_MAX_RETRIES + 1):
        try:
            async with _client() as client:
                resp = await client.request(method, path, **kwargs)
            if resp.status_code in _COGNEE_RETRYABLE_STATUS_CODES and attempt < _COGNEE_MAX_RETRIES:
                await asyncio.sleep(_COGNEE_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            return resp
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt < _COGNEE_MAX_RETRIES:
                await asyncio.sleep(_COGNEE_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            raise
    raise last_exc


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name.strip().lower())


def _run_prefix(run_id: str) -> str:
    return f"stocksage_run{_slug(run_id)}_"


def _sector_prefix(run_id: str) -> str:
    return f"{_run_prefix(run_id)}sector_"


def _company_prefix(run_id: str) -> str:
    return f"{_run_prefix(run_id)}company_"


def _sector_dataset(run_id: str, sector_name: str) -> str:
    return f"{_sector_prefix(run_id)}{_slug(sector_name)}"


def _company_dataset(run_id: str, ticker: str) -> str:
    return f"{_company_prefix(run_id)}{_slug(ticker)}"


async def _add_text(text: str, dataset_name: str, node_set: list[str]) -> dict:
    payload = {"textData": [text], "datasetName": dataset_name, "nodeSet": node_set}
    resp = await _request("POST", "/api/v1/add_text", json=payload)
    resp.raise_for_status()
    return resp.json()


async def _cognify(dataset_names: list[str]) -> dict:
    # Reverted from runInBackground=True: that change was meant to stop a
    # slow synchronous cognify from blocking us, but it didn't fix the
    # underlying latency (real runs still showed the same ~10min gaps) and
    # introduced a worse failure mode -- writing to (or deleting) a dataset
    # while its earlier background cognify job is still in flight can
    # return 409/500. Blocking mode's failure shape (client-side timeout)
    # is at least a known, already-handled one via _request's retry.
    payload = {"datasets": dataset_names, "runInBackground": False}
    resp = await _request("POST", "/api/v1/cognify", json=payload)
    resp.raise_for_status()
    return resp.json()


async def _list_datasets() -> list[dict]:
    resp = await _request("GET", "/api/v1/datasets/")
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


async def _list_datasets_stable(min_stable_checks: int = 2, max_attempts: int = 8, delay_seconds: float = 1.0) -> list[dict]:
    # GET /api/v1/datasets/ lags behind dataset creation -- observed live:
    # a dataset written moments earlier (add_text succeeded, and is
    # immediately findable via a direct per-dataset search) is sometimes
    # simply absent from this list for a few seconds. Anything that
    # discovers dataset names by listing (read_all_findings, cleanup_run,
    # improve_memory) needs the list to have caught up first, or it silently
    # misses recently-written data / leaves orphaned datasets behind. Polls
    # until the returned count is unchanged for `min_stable_checks` in a row.
    previous_count = -1
    stable_checks = 0
    datasets: list[dict] = []
    for _ in range(max_attempts):
        datasets = await _list_datasets()
        if len(datasets) == previous_count:
            stable_checks += 1
            if stable_checks >= min_stable_checks:
                break
        else:
            stable_checks = 0
        previous_count = len(datasets)
        await asyncio.sleep(delay_seconds)
    return datasets


async def _delete_dataset(dataset_id: str) -> None:
    resp = await _request("DELETE", f"/api/v1/datasets/{dataset_id}")
    resp.raise_for_status()


async def _search_chunks(dataset_name: str, query: str, node_name: list[str] | None = None, top_k: int = 50) -> list[dict]:
    payload = {
        "searchType": "CHUNKS",
        "datasets": [dataset_name],
        "query": query,
        "topK": top_k,
    }
    if node_name:
        payload["nodeName"] = node_name

    resp = await _request("POST", "/api/v1/search", json=payload)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    results = resp.json()

    chunks = []
    for result in results if isinstance(results, list) else []:
        search_result = result.get("search_result")
        if isinstance(search_result, list):
            for item in search_result:
                if isinstance(item, dict) and "text" in item:
                    chunks.append(item)
    return chunks


def _parse_envelopes(chunks: list[dict]) -> list[dict]:
    envelopes = []
    for chunk in chunks:
        try:
            envelope = json.loads(chunk["text"])
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if isinstance(envelope, dict):
            envelope["_created_at"] = chunk.get("created_at", 0)
            envelopes.append(envelope)
    return envelopes


async def write_sector_context(run_id: str, sector_name: str, data: dict) -> dict:
    dataset_name = _sector_dataset(run_id, sector_name)
    node_tag = f"sector:{_slug(sector_name)}"
    envelope = {"type": "sector_context", "sector_name": sector_name, "data": data}
    async with _write_lock(dataset_name):
        add_result = await _add_text(json.dumps(envelope), dataset_name, [node_tag])
        cognify_result = await _cognify([dataset_name])
    return {"dataset_name": dataset_name, "add": add_result, "cognify": cognify_result}


async def write_company_findings(run_id: str, ticker: str, layer: str, agent_name: str, data: dict) -> dict:
    dataset_name = _company_dataset(run_id, ticker)
    ticker_tag = f"company:{_slug(ticker)}"
    finding_tag = f"company:{_slug(ticker)}:{_slug(layer)}:{_slug(agent_name)}"
    envelope = {
        "type": "company_finding",
        "ticker": ticker,
        "layer": layer,
        "agent_name": agent_name,
        "data": data,
    }
    async with _write_lock(dataset_name):
        add_result = await _add_text(json.dumps(envelope), dataset_name, [ticker_tag, finding_tag])
        cognify_result = await _cognify([dataset_name])
    return {"dataset_name": dataset_name, "add": add_result, "cognify": cognify_result}


async def read_sector_context(run_id: str, sector_name: str) -> dict:
    dataset_name = _sector_dataset(run_id, sector_name)
    node_tag = f"sector:{_slug(sector_name)}"
    chunks = await _search_chunks(dataset_name, f"sector context for {sector_name}", node_name=[node_tag])
    envelopes = [e for e in _parse_envelopes(chunks) if e.get("type") == "sector_context"]
    if not envelopes:
        return {}
    latest = max(envelopes, key=lambda e: e["_created_at"])
    return latest["data"]


async def read_company_findings(run_id: str, ticker: str) -> dict:
    dataset_name = _company_dataset(run_id, ticker)
    ticker_tag = f"company:{_slug(ticker)}"
    chunks = await _search_chunks(dataset_name, f"findings for {ticker}", node_name=[ticker_tag])
    envelopes = [e for e in _parse_envelopes(chunks) if e.get("type") == "company_finding"]

    findings: dict = {"ticker": ticker}
    for envelope in envelopes:
        layer = envelope.get("layer", "unknown")
        agent_name = envelope.get("agent_name", "unknown")
        findings.setdefault(layer, {})[agent_name] = envelope["data"]
    return findings


async def read_all_findings(run_id: str) -> dict:
    sector_prefix = _sector_prefix(run_id)
    company_prefix = _company_prefix(run_id)
    datasets = await _list_datasets_stable()
    result: dict = {"sectors": {}, "companies": {}}

    for dataset in datasets:
        name = dataset.get("name", "")
        if name.startswith(sector_prefix):
            # Match read_sector_context's node_name-scoped search exactly --
            # an unscoped top_k search here was observed to miss content
            # that a node_name-scoped search on the same dataset finds
            # reliably. The dataset name's own suffix is the sector slug,
            # so the tag can be reconstructed without knowing the original
            # (unslugged) sector_name.
            sector_slug = name[len(sector_prefix) :]
            node_tag = f"sector:{sector_slug}"
            chunks = await _search_chunks(name, "sector context", node_name=[node_tag], top_k=100)
            envelopes = [e for e in _parse_envelopes(chunks) if e.get("type") == "sector_context"]
            for envelope in envelopes:
                result["sectors"][envelope.get("sector_name", name)] = envelope["data"]
        elif name.startswith(company_prefix):
            ticker_slug = name[len(company_prefix) :]
            ticker_tag = f"company:{ticker_slug}"
            chunks = await _search_chunks(name, "company findings", node_name=[ticker_tag], top_k=100)
            envelopes = [e for e in _parse_envelopes(chunks) if e.get("type") == "company_finding"]
            for envelope in envelopes:
                ticker = envelope.get("ticker", name)
                layer = envelope.get("layer", "unknown")
                agent_name = envelope.get("agent_name", "unknown")
                company_entry = result["companies"].setdefault(ticker, {})
                company_entry.setdefault(layer, {})[agent_name] = envelope["data"]

    return result


async def improve_memory(run_id: str, dataset_name: str | None = None) -> dict:
    if dataset_name:
        return await _cognify([dataset_name])
    run_prefix = _run_prefix(run_id)
    datasets = await _list_datasets_stable()
    run_names = [d["name"] for d in datasets if d.get("name", "").startswith(run_prefix)]
    if not run_names:
        return {}
    return await _cognify(run_names)


async def settle_run(run_id: str, delay_seconds: float = 8.0) -> dict:
    """Bounded settle step for the orchestrator to call ONCE, after all of a
    run's Layer 1/2 writes finish and before Synthesis reads.

    Cognee's indexing has an observed lag: a dataset's content can pass
    add_text+cognify successfully yet not be reliably searchable for some
    seconds after, especially under the concurrent write load of Layer 2's
    fan-out. Verifying and retrying *every individual write* to work around
    this was tried and measured -- it didn't reliably converge (still missed
    data even after multiple retries per write) while adding 100+ seconds of
    latency for a handful of test writes. This trades per-write precision
    for a single fixed delay plus one batch re-cognify pass across the whole
    run, bounding worst-case added latency instead of chasing it per call.

    Back to 8s (was bumped to 45s while cognify ran with runInBackground=True
    -- reverted, see _cognify's docstring, so that reasoning no longer applies).
    """
    await asyncio.sleep(delay_seconds)
    return await improve_memory(run_id)


async def cleanup_run(run_id: str) -> dict:
    run_prefix = _run_prefix(run_id)
    datasets = await _list_datasets_stable()
    matches = [d for d in datasets if d.get("name", "").startswith(run_prefix)]

    deleted = []
    failed = []
    for match in matches:
        try:
            await _delete_dataset(match["id"])
            deleted.append(match["name"])
        except httpx.HTTPStatusError as exc:
            # Observed live: deleting a dataset while its own cognify job is
            # still in flight can 500. One stuck dataset shouldn't break
            # cleanup for every other dataset in this run -- log and move on
            # rather than let this raise and abort the rest of the loop.
            print(f"[cognee_client] cleanup_run: failed to delete '{match['name']}': {exc}")
            failed.append(match["name"])

    return {"deleted_count": len(deleted), "deleted_datasets": deleted, "failed_datasets": failed}


if __name__ == "__main__":
    import asyncio
    import uuid

    async def _smoke_test():
        run_id = uuid.uuid4().hex[:12]
        print(f"run_id = {run_id}")

        sector_data = {"sector_name": "AI Infrastructure", "catalyst": "test catalyst"}
        company_data = {"ticker": "NVDA", "fundamental_signal": "STRONG"}

        await write_sector_context(run_id, "AI Infrastructure", sector_data)
        await write_company_findings(run_id, "NVDA", "layer2", "fundamental", company_data)

        read_sector = await read_sector_context(run_id, "AI Infrastructure")
        assert read_sector == sector_data, f"sector round-trip mismatch: {read_sector!r} != {sector_data!r}"
        print("sector round-trip OK:", read_sector)

        read_company = await read_company_findings(run_id, "NVDA")
        assert read_company["layer2"]["fundamental"] == company_data, (
            f"company round-trip mismatch: {read_company!r}"
        )
        print("company round-trip OK:", read_company)

        cleanup_result = await cleanup_run(run_id)
        print("cleanup result:", cleanup_result)

        after_cleanup = await read_all_findings(run_id)
        assert after_cleanup == {"sectors": {}, "companies": {}}, (
            f"expected empty findings after cleanup, got: {after_cleanup!r}"
        )
        print("read_all_findings after cleanup (expected empty):", after_cleanup)
        print("SMOKE TEST PASSED")

    asyncio.run(_smoke_test())

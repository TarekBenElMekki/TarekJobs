"""Bounded one-listing/one-detail diagnostics; only explicit artifact writes."""
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from .adapters.registry import create, info
from .artifacts import ArtifactStore
from .contracts import Checkpoint, Scope
from .storage import ROOT, is_production


def diagnose(key, database_path, artifact_root, *, sample_detail=True):
    target = Path(artifact_root).resolve()
    if is_production(Path(database_path)) or target == ROOT or (ROOT / "data").resolve() in (target, *target.parents):
        raise PermissionError("Diagnostics require explicit scratch database/artifact paths")
    if target == Path(database_path).resolve():
        raise ValueError("Artifact path cannot be the database path")
    run_id = "diagnostic_" + uuid4().hex
    store = ArtifactStore(target)
    adapter = create(key, target / run_id)
    try:
        scope = Scope(country="TN")
        partition = adapter.plan(scope)[0]
        page = adapter.inventory(scope, partition,
            Checkpoint(run_id, partition, None, 0, adapter.metadata.adapter_version))
        listing_path, listing_hash = store.write(adapter.metadata.source_id, run_id, "inventory", asdict(page))
        report = {**info(key), "kind": "bounded_diagnostic", "full_acquisition": False,
                  "database_writes": 0, "listing_pages_requested": 1,
                  "sample_keys": [r.key for r in page.records[:10]],
                  "records_on_first_page": len(page.records), "next_cursor": page.next_cursor,
                  "terminal": asdict(page.terminal) if page.terminal else None,
                  "error_code": page.error_code,
                  "listing_artifact": {"path": listing_path, "sha256": listing_hash},
                  "detail_pages_requested": 0}
        # Never continue after an access stop, even if a partial record was present.
        if sample_detail and page.records and not page.error_code and (
                page.terminal is None or page.terminal.state == "exhausted"):
            result = adapter.detail(page.records[0])
            path, digest = store.write(adapter.metadata.source_id, run_id, "details", asdict(result))
            report.update(detail_pages_requested=1,
                          detail_artifact={"path": path, "sha256": digest},
                          detail_disposition=result.disposition, detail_error=result.error_code)
        report["robots_observations"] = adapter.http.observations
        path, digest = store.write(adapter.metadata.source_id, run_id, "receipts", report)
        return {**report, "report_path": path, "report_sha256": digest}
    finally:
        adapter.close()

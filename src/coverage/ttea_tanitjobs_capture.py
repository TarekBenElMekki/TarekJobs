"""TTEA offline staged-capture adapter. Explicit scratch import; no network."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .artifacts import canonical_json
from .contracts import (AdapterMetadata, CanonicalHints, Capabilities, DetailResult,
                        InventoryPage, InventoryRecord, Partition, Scope, TerminalEvidence)
from .adapters.parsing import iso_date, work_mode
from .service import CoverageService
from .storage import Database, is_production


class TTEATanitjobsCaptureAdapter:
    # Keep the established Tanitjobs source identity; version/scope distinguish
    # manually captured public HTML from the ordinary HTTP acquisition.
    metadata = AdapterMetadata(
        "tanitjobs_tn", "Tanitjobs", "native_board", "public_html", "tanitjobs",
        "ttea-browser-capture-v1", "https://www.tanitjobs.com",
        Capabilities(detail_pages=True), canonical_hints=CanonicalHints(external_id=True))

    def __init__(self, snapshot_path):
        self.ttea_path = Path(snapshot_path).resolve()
        raw = self.ttea_path.read_bytes()
        self.ttea_digest = hashlib.sha256(raw).hexdigest()
        if self.ttea_path.stem != self.ttea_digest:
            raise ValueError("TTEA snapshot filename/hash mismatch")
        self.ttea_snapshot = json.loads(raw)
        if (self.ttea_snapshot.get("format") != "ttea-tanitjobs-stage-v1" or
                self.ttea_snapshot.get("source_id") != "tanitjobs_tn" or
                self.ttea_snapshot.get("coverage") != "manual_capture_set_only" or
                self.ttea_snapshot.get("site_exhausted") is not False):
            raise ValueError("TTEA snapshot contract mismatch")
        self.ttea_rows = self.ttea_snapshot["records"]
        self.ttea_by_id = {row["external_id"]: row for row in self.ttea_rows}
        if len(self.ttea_by_id) != len(self.ttea_rows):
            raise ValueError("TTEA snapshot contains duplicate identities")
        self.ttea_scope = Scope(country="TN", region="ttea-browser-capture:" + self.ttea_digest)

    def plan(self, scope):
        if scope != self.ttea_scope:
            raise ValueError("TTEA manual snapshot must use its explicit bounded capture-set scope")
        return (Partition("ttea-capture-set", {"ttea_snapshot_sha256": self.ttea_digest,
                    "acquisition": "human_browser_capture", "site_exhaustion_claimed": False}),)

    def _ttea_evidence(self, reference):
        reference = json.loads(reference)
        root = self.ttea_path.parent.parent
        path = (root / reference["path"]).resolve()
        if not path.is_relative_to(root) or path.parent != root / "ttea_evidence":
            raise ValueError("TTEA evidence path escapes staging")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise ValueError("TTEA capture evidence hash mismatch")
        return json.loads(raw)

    def _ttea_record(self, row, detail=False):
        name = "detail_json" if detail else "listing_json"
        if not row.get(name):
            if detail:
                return None, None
            name = "detail_json"  # Detail-first capture still creates inventory.
        record = json.loads(row[name])
        parsed = urlsplit(record["source_url"])
        match = re.fullmatch(r"/job/(\d+)(?:/[^/?#]*)?/?", parsed.path)
        if (parsed.scheme != "https" or parsed.netloc != "www.tanitjobs.com" or
                parsed.query or parsed.fragment or not match or match.group(1) != row["external_id"] or
                record["external_id"] != row["external_id"]):
            raise ValueError("TTEA captured job identity invalid")
        evidence = self._ttea_evidence(row[name.replace("_json", "_evidence")])
        if evidence.get("source_id") != "tanitjobs_tn" or record not in evidence.get("records", []):
            raise ValueError("TTEA snapshot record is not backed by capture evidence")
        return record, evidence

    def inventory(self, scope, partition, checkpoint):
        if partition != self.plan(scope)[0] or checkpoint.adapter_version != self.metadata.adapter_version:
            raise ValueError("TTEA checkpoint identity mismatch")
        offset = (checkpoint.cursor or {}).get("offset", 0)
        if type(offset) is not int or offset != checkpoint.pages_processed * 100:
            raise ValueError("TTEA snapshot cursor mismatch")
        selected = self.ttea_rows[offset:offset + 100]
        records = []
        for row in selected:
            raw, evidence = self._ttea_record(row)
            records.append(InventoryRecord(row["external_id"], row["source_url"], row["external_id"],
                {"title": raw["title"], "_ttea_capture": raw, "_ttea_evidence": evidence}))
        end = offset + len(selected)
        terminal = TerminalEvidence("exhausted", "ttea_manual_capture_set_consumed",
            {"ttea_snapshot_sha256": self.ttea_digest, "site_exhaustion_claimed": False,
             "scope": asdict(self.ttea_scope), "snapshot_records": len(self.ttea_rows)}) if end == len(self.ttea_rows) else None
        return InventoryPage(tuple(records), None if terminal else {"offset": end}, terminal,
            evidence={"ttea_snapshot_sha256": self.ttea_digest, "acquisition": "manual_browser_capture"})

    def detail(self, record):
        row = self.ttea_by_id[record.key]
        raw, evidence = self._ttea_record(row, detail=True)
        if raw is None:
            return DetailResult(error_code="ttea_detail_capture_pending",
                evidence={"source_id": "tanitjobs_tn", "source_record_key": record.key,
                          "action": "User must open job manually and capture selected job; import a new snapshot."})
        listing, _ = self._ttea_record(row)
        fields = {**listing, **{k: v for k, v in raw.items() if v}}
        values = {"title": fields["title"], "company": fields.get("company") or "Unknown",
                  "location": fields.get("location", ""), "description": raw["description"],
                  "employment_type": fields.get("employment", ""),
                  "canonical_url": raw["source_url"], "application_url": raw["source_url"],
                  "_application": {"kind": "detail_fallback", "url": raw["source_url"]},
                  "_freshness": {"state": "verified_live", "evidence": "manual_rendered_detail"},
                  "last_verified_at": evidence["captured_at"]}
        date = iso_date(fields.get("posted_date"))
        if date:
            values["posted_at"] = date
        mode = work_mode(fields.get("work_mode"))
        if mode:
            values.update(work_mode=mode, remote=mode == "remote")
        # No country, education or experience is inferred from arbitrary prose.
        return DetailResult(values, evidence={"ttea_raw_capture": raw,
            "ttea_capture_evidence": evidence, "ttea_snapshot_sha256": self.ttea_digest})


def ttea_import_snapshot(database_path, snapshot_path, artifact_root, *, run_id=None):
    database = Database(database_path)
    if is_production(database.path):
        raise PermissionError("TTEA browser capture importer is scratch-only; production import is separate")
    root = Path(artifact_root).resolve()
    # Do not write artifacts into the application's live data tree either.
    live_data = Path(__file__).resolve().parents[2] / "data"
    if root.is_relative_to(live_data.resolve()):
        raise PermissionError("TTEA capture import requires scratch artifacts outside live data")
    adapter = TTEATanitjobsCaptureAdapter(snapshot_path)
    service = CoverageService(database, root)
    run_id = run_id or service.start(adapter, adapter.ttea_scope, scan_kind="delta")
    return service.run(adapter, run_id)


def ttea_main():
    parser = argparse.ArgumentParser(description="TTEA explicit offline browser-capture scratch import")
    parser.add_argument("--db", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--artifacts", required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    print(canonical_json(ttea_import_snapshot(args.db, args.snapshot, args.artifacts, run_id=args.run_id)))


if __name__ == "__main__":
    ttea_main()

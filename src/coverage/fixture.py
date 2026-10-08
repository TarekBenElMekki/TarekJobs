"""Local JSONL reference adapter. One header followed by one record per line."""
from __future__ import annotations

import json
from pathlib import Path

from .artifacts import sha256
from .contracts import (
    AdapterMetadata, CanonicalHints, Capabilities, Checkpoint, DetailResult,
    InventoryPage, InventoryRecord, Limits, Partition, Scope, TerminalEvidence,
)


class FixtureAdapter:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        with self.path.open("rb") as stream:
            import hashlib
            self.digest = hashlib.file_digest(stream, "sha256").hexdigest()
        with self.path.open(encoding="utf-8-sig") as stream:
            self.header = json.loads(stream.readline())
        if self.header.get("format") != "coverage-fixture-v1":
            raise ValueError("Unsupported fixture format")
        self.page_size = self.header.get("page_size", 2)
        if type(self.page_size) is not int or self.page_size < 1:
            raise ValueError("Invalid fixture page size")
        source = self.header["source"]
        self._metadata = AdapterMetadata(
            source_id=source["source_id"], source_name=source["source_name"],
            source_type="fixture", acquisition_method="local_jsonl",
            adapter_key="local_fixture", adapter_version="1:" + self.digest,
            capabilities=Capabilities(detail_pages=True),
            canonical_hints=CanonicalHints(True, True, True), limits=Limits(),
        )

    @property
    def metadata(self):
        return self._metadata

    def plan(self, scope: Scope):
        return tuple(Partition(p) for p in self.header.get("partitions", ["all"]))

    def _entries(self, partition: str | None = None):
        # Streaming from disk: only one inventory page is held in memory.
        with self.path.open(encoding="utf-8-sig") as stream:
            next(stream)
            for line in stream:
                if line.strip():
                    item = json.loads(line)
                    if partition is None or item.get("partition","all") == partition:
                        yield item

    def inventory(self, scope: Scope, partition: Partition, checkpoint: Checkpoint):
        if checkpoint.adapter_version != self.metadata.adapter_version:
            raise ValueError("Fixture changed; checkpoint is incompatible")
        offset = (checkpoint.cursor or {}).get("offset",0)
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid fixture cursor")
        selected = []
        count = 0
        for number, item in enumerate(self._entries(partition.key)):
            count += 1
            if offset <= number < offset+self.page_size:
                selected.append(InventoryRecord(
                    item["key"], item["source_url"], item.get("external_id"), item.get("values",{})))
        if offset > count:
            raise ValueError("Cursor exceeds fixture boundary")
        finished = offset+len(selected) >= count
        terminal = None
        if finished:
            configured = self.header.get("terminal", {})
            terminal = TerminalEvidence(
                configured.get("state","exhausted"), configured.get("reason","local_fixture_eof"),
                {"fixture_sha256":self.digest,"records_in_partition":count,
                 "boundary_offset":offset+len(selected), **configured.get("evidence",{})})
        return InventoryPage(tuple(selected), None if finished else {"offset":offset+len(selected)},
                             terminal, max(1,(count+self.page_size-1)//self.page_size))

    def detail(self, record: InventoryRecord):
        for item in self._entries():
            if item["key"] == record.key:
                if item.get("detail_error"):
                    return DetailResult(error_code="fixture_detail_failure")
                return DetailResult({**item.get("values",{}), **item.get("detail",{})})
        return DetailResult(error_code="fixture_record_missing")

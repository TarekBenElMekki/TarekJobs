"""Typed source contract; metadata inspection never acquires data."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

JSON = dict[str, Any]


@dataclass(frozen=True)
class Scope:
    country: str = "*"
    work_mode: str = "*"
    region: str = ""


@dataclass(frozen=True)
class Partition:
    key: str = "all"
    metadata: JSON = field(default_factory=dict)


@dataclass(frozen=True)
class Capabilities:
    pagination: bool = True
    posted_date_filter: bool = False
    country_filter: bool = False
    location_filter: bool = False
    category_filter: bool = False
    detail_pages: bool = False
    official_api: bool = False
    feed: bool = False


@dataclass(frozen=True)
class Limits:
    maximum_results_per_query: int | None = None
    requests_per_minute: int | None = None


@dataclass(frozen=True)
class CanonicalHints:
    external_id: bool = False
    application_url: bool = False
    ats_requisition_id: bool = False


@dataclass(frozen=True)
class AdapterMetadata:
    source_id: str
    source_name: str
    source_type: str
    acquisition_method: str
    adapter_key: str
    adapter_version: str
    base_url: str | None = None
    capabilities: Capabilities = field(default_factory=Capabilities)
    limits: Limits = field(default_factory=Limits)
    canonical_hints: CanonicalHints = field(default_factory=CanonicalHints)


@dataclass(frozen=True)
class InventoryRecord:
    key: str
    source_url: str
    external_id: str | None = None
    values: JSON = field(default_factory=dict)


@dataclass(frozen=True)
class TerminalEvidence:
    state: str
    reason: str
    evidence: JSON

    def __post_init__(self):
        if self.state not in {"exhausted", "blocked", "manual_only", "unavailable"}:
            raise ValueError("Invalid terminal classification")
        if not self.reason.strip() or not self.evidence:
            raise ValueError("Terminal classification requires supporting evidence")


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    partition: Partition
    cursor: JSON | None
    pages_processed: int
    adapter_version: str
    completed: bool = False


@dataclass(frozen=True)
class InventoryPage:
    records: tuple[InventoryRecord, ...]
    next_cursor: JSON | None
    terminal: TerminalEvidence | None = None
    pages_expected: int | None = None
    evidence: JSON = field(default_factory=dict)
    content_fingerprint: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class DetailEmission:
    record: InventoryRecord
    requires_detail: bool = False


@dataclass(frozen=True)
class DetailResult:
    values: JSON = field(default_factory=dict)
    error_code: str | None = None
    disposition: str = "record"
    emissions: tuple[DetailEmission, ...] = ()
    evidence: JSON = field(default_factory=dict)

    def __post_init__(self):
        if self.disposition not in {"record", "excluded", "expanded"}:
            raise ValueError("Invalid detail disposition")
        if self.disposition != "expanded" and self.emissions:
            raise ValueError("Only expanded results emit child records")
        if len(self.emissions) > 1000:
            raise ValueError("Detail emission safety bound exceeded")


class AcquisitionFailure(RuntimeError):
    def __init__(self, code: str, evidence: JSON):
        super().__init__(code)
        self.code, self.evidence = code, evidence


class SourceAdapter(Protocol):
    @property
    def metadata(self) -> AdapterMetadata: ...

    def plan(self, scope: Scope) -> tuple[Partition, ...]: ...

    def inventory(self, scope: Scope, partition: Partition,
                  checkpoint: Checkpoint) -> InventoryPage: ...

    def detail(self, record: InventoryRecord) -> DetailResult: ...

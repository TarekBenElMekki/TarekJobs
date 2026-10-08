"""Native adapter metadata/factories; listing and info perform no acquisition."""
from dataclasses import asdict
from pathlib import Path

from .http import HttpPolicy, JsonAudit, PublicHttp
from .keejob import KeejobAdapter
from .tanitjobs import TanitjobsAdapter
from .tunisietravail import TunisieTravailAdapter

ADAPTERS = {cls.metadata.adapter_key: cls for cls in
            (TanitjobsAdapter, KeejobAdapter, TunisieTravailAdapter)}


def info(key):
    cls = ADAPTERS[key]
    return {"metadata": asdict(cls.metadata), "listing_url": cls.listing_url,
            "scope": {"country": "TN", "work_mode": "*", "region": ""},
            "boundary_strategy": cls.boundary_strategy,
            "migration_requirements": ["004_coverage_core.sql", "005_coverage_record_outcomes.sql"],
            "access_observation": "ordinary HTTP 403/challenge; no bypass" if key == "tanitjobs"
            else "public HTML accessible during limited pre-implementation inspection"}


def create(key, artifact_root):
    from urllib.parse import urlsplit
    cls = ADAPTERS[key]
    host = urlsplit(cls.listing_url).hostname
    policy = HttpPolicy(retry_rate_limits=False, max_redirects=0) if key == "keejob" else HttpPolicy()
    http = PublicHttp({host, host.removeprefix("www.")}, policy=policy,
                      audit=JsonAudit(Path(artifact_root).resolve() / "http_events.jsonl"))
    return cls(http=http)

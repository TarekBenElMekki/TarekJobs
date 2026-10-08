"""Atomic, content-addressed acquisition evidence; no response bodies in logs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_id(value) -> str:
    return sha256(canonical_json(value).encode("utf-8"))


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}", value):
        raise ValueError("Unsafe or empty coverage identifier")
    if value.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *("COM"+str(i) for i in range(1,10)), *("LPT"+str(i) for i in range(1,10))}:
        raise ValueError("Reserved filesystem identifier")
    if value.endswith("."):
        raise ValueError("Trailing dot in identifier")
    return value


class ArtifactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def write(self, source_id: str, run_id: str, kind: str, value) -> tuple[str, str]:
        if kind not in {"inventory", "details", "receipts"}:
            raise ValueError("Unknown artifact kind")
        directory = self.root / identifier(source_id) / identifier(run_id) / kind
        if not directory.resolve().is_relative_to(self.root):
            raise ValueError("Artifact directory escapes root")
        data = (canonical_json(value) + "\n").encode("utf-8")
        digest = sha256(data)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (digest + ".json")
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError("Artifact content/hash conflict")
            return str(target), digest
        fd, temporary = tempfile.mkstemp(prefix=".publish-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Same bytes always use the same destination. Atomic replacement is
            # safe for concurrent writers of identical content, across Windows/POSIX.
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return str(target), digest

    def read(self, path: str, digest: str):
        target = Path(path).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("Artifact is outside the pinned run root")
        data = target.read_bytes()
        if sha256(data) != digest:
            raise ValueError("Artifact SHA256 mismatch")
        return json.loads(data)


from contextlib import contextmanager


@contextmanager
def run_lock(database_path: Path, run_id: str):
    """Reject concurrent workers for one run; OS releases the lock on crash."""
    directory = database_path.parent / ".coverage_locks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (stable_id([str(database_path.resolve()),identifier(run_id)]) + ".lock")
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Coverage run already has an active worker") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

"""Load and verify a frozen snapshot corpus."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .models import CachedSource, SnapshotManifest, SourceManifestEntry


class SnapshotError(ValueError):
    """Snapshot manifest, cache, or integrity failure."""


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class SnapshotCorpus:
    def __init__(self, manifest_path: str | Path) -> None:
        self.manifest_path = Path(manifest_path)
        self.root = self.manifest_path.parent
        try:
            self.manifest = SnapshotManifest.model_validate_json(
                self.manifest_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise SnapshotError(f"invalid manifest {self.manifest_path}: {exc}") from exc
        self._entries = {
            source.source_id: source for source in self.manifest.sources
        }
        self._documents: dict[str, CachedSource] = {}

    def entry(self, source_id: str) -> SourceManifestEntry:
        try:
            return self._entries[source_id]
        except KeyError as exc:
            raise SnapshotError(f"unknown source_id {source_id}") from exc

    def cache_path(self, entry: SourceManifestEntry) -> Path:
        path = (self.root / entry.cache_relpath).resolve()
        if self.root.resolve() not in path.parents:
            raise SnapshotError(
                f"cache path escapes snapshot root: {entry.cache_relpath}"
            )
        return path

    def document(self, source_id: str) -> CachedSource:
        if source_id in self._documents:
            return self._documents[source_id]
        entry = self.entry(source_id)
        path = self.cache_path(entry)
        if not path.exists():
            raise SnapshotError(
                f"missing frozen cache for {source_id}: {path}. "
                "Re-fetching is allowed only if the resulting hash matches "
                f"{entry.content_hash}."
            )
        try:
            document = CachedSource.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise SnapshotError(f"invalid cached source {path}: {exc}") from exc
        if document.source_id != source_id:
            raise SnapshotError(
                f"cache source_id={document.source_id} does not match {source_id}"
            )
        observed_hash = content_hash(document.cleaned_text)
        if observed_hash != entry.content_hash:
            raise SnapshotError(
                f"hash mismatch for {source_id}: expected {entry.content_hash}, "
                f"observed {observed_hash}"
            )
        self._documents[source_id] = document
        return document

    def verify_all(self) -> None:
        for source_id in self._entries:
            self.document(source_id)

    def entries(self) -> list[SourceManifestEntry]:
        return list(self._entries.values())

"""Create and verify copyright-aware frozen snapshot manifests."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .corpus import SnapshotCorpus, content_hash
from .models import (
    CachedSource,
    RedistributionPolicy,
    SnapshotManifest,
    SourceManifestEntry,
    SourceType,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="add one cleaned source")
    add_parser.add_argument("--manifest", type=Path, required=True)
    add_parser.add_argument("--cleaned-text", type=Path, required=True)
    add_parser.add_argument("--source-id", required=True)
    add_parser.add_argument("--title", required=True)
    add_parser.add_argument("--url", required=True)
    add_parser.add_argument("--retrieved-at", required=True)
    add_parser.add_argument("--source-type", choices=list(SourceType), required=True)
    add_parser.add_argument("--version-or-pub-date")
    add_parser.add_argument(
        "--redistribution-policy",
        choices=list(RedistributionPolicy),
        default=RedistributionPolicy.CACHE_ONLY,
    )
    add_parser.add_argument("--language", default="en")
    add_parser.add_argument("--license")
    add_parser.add_argument("--excerpt-chars", type=int, default=500)

    verify_parser = subparsers.add_parser("verify", help="verify every cache hash")
    verify_parser.add_argument("--manifest", type=Path, required=True)
    return parser


def _load_or_create_manifest(path: Path, created_at: datetime) -> SnapshotManifest:
    if path.exists():
        return SnapshotManifest.model_validate_json(path.read_text(encoding="utf-8"))
    return SnapshotManifest(
        snapshot_id=path.parent.name,
        created_at=created_at,
        sources=[],
    )


def add_source(args: argparse.Namespace) -> None:
    if not 1 <= args.excerpt_chars <= 1200:
        raise SystemExit("--excerpt-chars must be between 1 and 1200")
    retrieved_at = datetime.fromisoformat(args.retrieved_at.replace("Z", "+00:00"))
    cleaned_text = args.cleaned_text.read_text(encoding="utf-8")
    manifest = _load_or_create_manifest(args.manifest, retrieved_at)
    if any(source.source_id == args.source_id for source in manifest.sources):
        raise SystemExit(f"source_id already exists: {args.source_id}")

    cache_relpath = f"cache/{args.source_id}.json"
    entry = SourceManifestEntry(
        source_id=args.source_id,
        title=args.title,
        canonical_url=args.url,
        retrieved_at=retrieved_at,
        content_hash=content_hash(cleaned_text),
        excerpt=cleaned_text[: args.excerpt_chars].strip(),
        source_type=args.source_type,
        version_or_pub_date=args.version_or_pub_date,
        redistribution_policy=args.redistribution_policy,
        cache_relpath=cache_relpath,
        language=args.language,
        license=args.license,
    )
    cache_path = args.manifest.parent / cache_relpath
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        CachedSource(
            source_id=args.source_id,
            cleaned_text=cleaned_text,
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    updated = manifest.model_copy(update={"sources": [*manifest.sources, entry]})
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        updated.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "source_id": entry.source_id,
                "content_hash": entry.content_hash,
                "cache_path": str(cache_path),
                "manifest": str(args.manifest),
            },
            indent=2,
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "add":
        add_source(args)
        return 0
    corpus = SnapshotCorpus(args.manifest)
    corpus.verify_all()
    print(
        json.dumps(
            {
                "valid": True,
                "snapshot_id": corpus.manifest.snapshot_id,
                "sources": len(corpus.entries()),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

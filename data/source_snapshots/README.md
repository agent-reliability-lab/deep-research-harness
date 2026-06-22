# Frozen source snapshots

The public repository commits:

- `manifest.json`: source metadata, retrieval time, SHA-256 content hash,
  short excerpt, source type, version/publication date, and redistribution
  policy;
- no third-party full text unless its license explicitly permits
  redistribution.

Full cleaned text for `cache_only` sources lives under `cache/`, which is
gitignored. The cache is required to execute BM25 search and source reading.

## Add a cleaned source

```bash
python -m src.snapshots.cli add \
  --manifest data/source_snapshots/manifest.json \
  --cleaned-text /path/to/local-cleaned-text.txt \
  --source-id mem0-docs-memory \
  --title "Mem0 Memory Documentation" \
  --url "https://docs.mem0.ai/..." \
  --retrieved-at "2026-06-21T12:00:00Z" \
  --source-type official_docs \
  --version-or-pub-date "2026-06-21" \
  --excerpt "A short, source-faithful excerpt suitable for public metadata." \
  --redistribution-policy cache_only
```

## Verify the local cache

```bash
python -m src.snapshots.cli verify \
  --manifest data/source_snapshots/manifest.json
```

If a cache is missing, the source may be fetched again only when the cleaned
text reproduces the committed hash. If the upstream page changed and the hash
does not match, verification fails closed; the manifest cannot reconstruct the
old copyrighted text by itself. The public snapshot is therefore
integrity-verifiable, not a self-contained archival copy.

# Memory architecture MVP-2 snapshot

`memory-architecture-mvp2-2026-06-22-v1` is the enlarged corpus for the
context-stressing C0 task. It is a new snapshot rather than a mutation of
`memory-systems-2026-06`, so the original two-task development baseline remains
reproducible.

## Public metadata

- 17 sources: 14 official documentation pages and 3 canonical arXiv papers
- products represented: Mem0, Letta/MemGPT, Zep/Graphiti, and Cognee
- cleaned corpus size: 322,741 characters and 43,627 whitespace-delimited words
- heuristic token range: approximately 58k-81k before tool-message overhead

The token range is only a sizing estimate. The acceptance gate remains a real
C0 trace with `peak_active_context_tokens >= 60000`.

## Extraction boundary

- Mintlify documentation was fetched from each official `.md` endpoint and
  passed through the deterministic Markdown cleaner.
- The Mem0 and Zep papers were extracted from official arXiv HTML after the PDF
  title and representative pages were visually checked.
- MemGPT, whose arXiv HTML endpoint was unavailable, was extracted from the
  canonical arXiv PDF after visual inspection.
- Full cleaned text and fetched source files live under gitignored `cache/`.
- The committed manifest contains only metadata, SHA-256 hashes, and short
  source-faithful excerpts.

## Verify locally

```bash
python -m src.snapshots.cli verify \
  --manifest data/source_snapshots/memory-architecture-mvp2-2026-06-22-v1/manifest.json
```

# MVP-2 Step 1 — Context-stressing task + enlarged corpus

> The keystone. Goal: a deterministic task whose C0 run records `peak_active_context_tokens ≥ 60,000`, so the 60% compaction trigger can actually fire. Until this exists, every C1/C2/C3 delta is structurally null.
> Division of labor: **you** fetch/clean/hash/freeze the sources (live web + source-authority judgment); **I** author the task claims against the *frozen* text (the Day-3 lesson: write patterns against frozen bytes, not live pages).

## 1. Token-budget math (why this size)

- Budget `max_input_tokens = 100,000`; trigger at 60% = **60,000**. Today's C0 peak: **11,779** with 3 small sources.
- The runner re-sends the full message list every turn, so context accumulates ≈ `base(~2k) + Σ per-read(cleaned_text + evidence + tool overhead)`.
- Target a **projected peak ~75–80k** (margin above 60k), then **verify empirically** with a real C0 run — do not trust the math alone.

**Sizing**: ~**14–18 sources at 3–5k cleaned tokens each**, task forces reading **≥14** of them. Official memory-system docs/papers are naturally this long, so real sources at natural length clear it comfortably.

> ⚠️ **P4 finding to check first** (this is a Day-3 token-efficiency issue, and it changes the math): the C0 fixture trace showed `read_source` returning BOTH `text` and `cleaned_text` with identical content — that **doubles** per-read context. If real (`src/tools/runtime.py`), it's a genuine token-efficiency bug worth fixing for production. **Decide:** fix it now (cleaner; size corpus assuming single return, as above) — or leave it (you'd need ~half the sources to cross the trigger, but you ship a known inefficiency). Recommend **fix + size for single return**.

## 2. Corpus shopping list (~16 sources)

> **Honesty:** only the 3 frozen URLs below are confirmed. For the rest I give product × topic — **you locate the official doc/paper URL and confirm it's official before freezing.** I am deliberately NOT inventing arXiv IDs or exact doc URLs.

| # | Product | Topic | Status |
|---|---|---|---|
| 1 | Mem0 | memory-evaluation | ✅ FROZEN |
| 2 | Mem0 | platform/architecture overview | fetch |
| 3 | Mem0 | graph memory | fetch |
| 4 | Mem0 | research paper (the "scalable long-term memory for agents" paper) | fetch (find canonical arXiv/site URL) |
| 5 | Letta | memory-blocks | ✅ FROZEN |
| 6 | Letta | archival-memory | ✅ FROZEN |
| 7 | Letta | core/context memory concepts | fetch |
| 8 | Letta | agent memory overview | fetch |
| 9 | Letta | MemGPT paper (LLMs-as-OS) | fetch (find canonical URL) |
| 10 | Zep | Graphiti docs (temporal knowledge graph) | fetch |
| 11 | Zep | Zep paper (temporal KG architecture for agent memory) | fetch (find canonical URL) |
| 12 | Zep | memory/retrieval concepts | fetch |
| 13 | Cognee | architecture/overview | fetch |
| 14 | Cognee | memory / knowledge-graph concepts | fetch |
| 15 | Cognee | pipeline/ingestion docs | fetch |
| 16 | (spare) | any of the above with a second canonical page | optional, for margin |

Freeze each into the existing snapshot pipeline: cleaned_text → SHA-256 → manifest (URL + hash + short excerpt); full text stays in the gitignored cache. Same copyright boundary as before.

## 3. Stressing-task design spec (`memory-architecture-survey-01`)

- **evaluation_mode**: `deterministic_benchmark` — **critical**: scores with NO judge, keeps the judge off the MVP-2 critical path.
- **family**: `architecture_comparison`
- **prompt** (draft): *"Survey and compare the memory architectures of Mem0, Letta, Zep, and Cognee across four dimensions: (a) where persistent memory sits relative to the active context, (b) write/update semantics, (c) retrieval mechanism, (d) temporal handling. Cite frozen-source evidence for every claim. Do not recommend a winner; identify the architectural trade-offs."*
- **required_claims**: ~**14–16**, ≥3 per product, **each tied to a distinct source_id** → to cover them all the agent must read ≥14 sources (this is the mechanism that drives peak context past the trigger). Each claim: `evidence_patterns` (verbatim strings from frozen text) + `answer_patterns`, `verification_status: draft` until checked.
- **known_distractors** (plausible-but-wrong cross-system confusions — the real test of understanding):
  - "Cognee keeps memory in the context window like Letta blocks" (false)
  - "Zep is stateless / has no temporal model" (false)
  - "Mem0 uses a temporal knowledge graph like Zep" (false)
  - "Letta archival memory is always loaded into the prompt" (false — reused, still false)
- **rubric**: coverage 1.0, citation precision 1.0, entailment 1.0, `max_distractor_mentions: 0`.
- **citation_expectations**: `minimum_citations: ~14`, `minimum_unique_sources: ~14`.

## 4. Execution order (who does what)

1. **You** — (optional but recommended) fix the `read_source` double-return; fetch + clean + hash + freeze the ~13 new sources into the snapshot.
2. **Me** — author the full `memory-architecture-survey-01.json` (claims / evidence_patterns / answer_patterns / distractors / rubric) **against the frozen cleaned_text**, so every pattern is a verbatim string in the frozen bytes.
3. **You + preflight** — run `preflight-patterns` against the frozen copies → confirm all patterns hit (the 38/38-style gate).
4. **Freeze the task** (lifecycle=frozen, pin source_snapshot_id, verify claims).
5. **Run C0 once** on it → **confirm `peak_active_context_tokens ≥ 60,000`** in the trace. ← the step-1 done-gate.

If C0 peak comes in under 60k: the corpus/task is too small — add sources or require more reads. **Do not proceed to C1 until the trigger demonstrably has headroom to fire.**

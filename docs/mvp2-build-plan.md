# MVP-2 Build Plan

> Sequenced, measurement-valid plan to reach MVP-2 (C0–C3 ablation + recovery + result table).
> Source: design workflow (judge + C1–C3 + tasks/recovery + adversarial null-result critique).
> Companion to [`spec.md`](../spec.md). Status: C0 done + first real development EGTSR; building C1–C3.

## Keystone (the one thing that decides whether MVP-2 means anything)

**The deltas are the product, and right now they are structurally zero.** Verified in code: C0's `peak_active_context_tokens = 11,779` (`results/processed/c0-development-frozen-v1.json`) against `max_input_tokens = 100,000` ≈ **12%**. The compaction trigger fires at **60%**. So on the current 3-doc corpus + 2 frozen tasks, **a C0→C1 comparison is guaranteed null** — compaction never fires, peak context cannot move. Building C1/C2/C3 first would produce a clean-looking 80-run matrix whose every cell shows ~zero delta: a *misleading* result, not a missing one.

**Therefore: design and freeze the stressing task + enlarged corpus BEFORE building any control**, and mirror this per control (permission-bait fixture before C2, coverage-breadth task before C3).

Second keystone: **the judge is an instrument, not a feature.** The two `architecture-*` tasks are `judge_required`; `evaluate_deterministic_run` already refuses to score them. MVP-2 is reachable **without** the judge — the deterministic tasks give a non-empty scored core; `judge_required` cells are carried as explicit status labels (which MVP-2 acceptance permits).

## Sequence (keystone-first)

| Step | Deliverable | Done when | Effort |
|---|---|---|---|
| **1** | Enlarged frozen corpus (12–20 official docs/papers, 1.5–4k tokens each) + deterministic multi-read stressing task `memory-architecture-survey-01` (NOT judge_required), frozen | A C0 run on it records `peak_active_context_tokens ≥ 60,000` | M (1–1.5d) |
| **2** | C1 compaction behind `configs/compaction.yaml` (trigger 0.60); `src/agent/compaction.py` + C1Runner forks C0Runner; emit `CompactionEvent` + `CheckpointEvent` | CompactionEvent fires (ratio < 1.0) AND post-compaction run still cites every answer-critical evidence_id (100% retention) AND summarizer cost visible in both ModelCallEvent + CompactionEvent | L (2–3d) |
| **3** | C2 permission gate behind `configs/permission.yaml` (spec §11 allow/ask/deny + simulated approval) + **threat fixture** | On the threat fixture: ≥1 correct deny/ask with action blocked, decision in trace, benign action NOT over-blocked; FP/FN counts reported | M-L (2d) |
| **4** | C3 sub-agents behind `configs/subagents.yaml`; workers return ONLY the §13 structured handoff; parent validates evidence_ids + **coverage-breadth task** | Validated handoff (parent accepts only after evidence_id validation); handoff bounded vs full transcript; coverage ≥ C2's; C3 cost reported separately | L (2–3d) |
| **5** | One interruption/recovery test (spec §14): inject, resume from CheckpointState, verify plan+evidence+citation-mapping restored, no re-run gated actions, within budget | A resumed run on the stressing task restores state and completes; recovery_success recorded | S-M (1–1.5d) |
| **6** | Results table + badcase taxonomy across the matrix/labeled subset (both north-stars + 4 supporting; §16 failure label on every unsuccessful eval-valid run) | One command reproduces the table; judge_required cells labeled not blank; ≥1 recovery pass shown | M (1–2d) |
| **7** | *(Optional)* GJ judge calibration gate: 20–30 item human-labeled gold set + GJ1–GJ6 (kappa ≥ 0.70, accuracy ≥ 0.90, **zero false-positive on negatives**, K=5 self-consistency ≥ 95%) | GJ PASS AND architecture-01/02 flip judge_required → scored under a pinned judge version | L-XL (3d+, defer if tight) |

## Measurement-validity guardrails (build these in)

1. **Trigger must actually fire** — C0 peak ≥ 60,000 on the stressing task BEFORE C1 is trusted. If it stays near 11,779, the fixture is too small; fix it, don't report the delta.
2. **Compaction cost never hidden** — summarizer call logged as its own ModelCallEvent AND mirrored in CompactionEvent; agent vs judge cost are separate line items.
3. **Critical-fact retention is a gate** — a fact may leave the window only if reconstructable (reloadable EvidenceRecord or in a CheckpointState field). Dropping an answer-critical evidence_id = `compaction_information_loss`.
4. **Judge never enters EGTSR uncalibrated** — judge_required/judge_pending/judge_failed cells excluded from denominator, surfaced with explicit labels. A judge PASS may not override a deterministic FAIL (task_success ANDed in code).
5. **Judge version pinned constant across C0–C3** — judge_prompt + rubric + model_id + calibration_artifact_id; any change invalidates and forces GJ re-pass (so EGTSR delta = harness, not judge drift).
6. **Judge fail-closed** — tau_conf=0.70, low confidence → unsupported; grounded_quote must be a verbatim substring of the frozen excerpt; judge is a different/stronger model family than the agent (avoid self-preference).
7. **Distractor mention = deterministic kill-switch** — exact case-folded scan before any judge call; max_distractor_mentions=0 is a deterministic FAIL the judge can't rescue.
8. **Scope isolation holds** — aggregator rejects mixed evaluation_scope; calibration gold-set runs stay in development/fixture scope, never reach primary.
9. **Infra vs capability** — judge API/JSON failure = judge_failed_run → INFRA_API_FAILED (excluded); budget exhaustion stays agent_failed with full trace.
10. **Per-control isolation** — each control needs its OWN stressing fixture or its delta is null (compaction→context-bloat task, C2→permission-bait, C3→coverage-breadth).

## What MVP-2 CAN claim

Four cumulative configs behind one feature-flagged interface, on a frozen reproducible corpus, measuring how each control changes EGTSR / cost-per-success / peak-context / citation precision / claim coverage / recovery — **on stressing tasks designed so each control is actually exercised**, with infra failures separated from agent failures and ≥1 recovery test passing. A **directional, attributable** result (e.g. "compaction cut peak context X% at one summarizer call/fire, retaining 100% of answer-critical facts").

## What MVP-2 CANNOT claim

- **Statistical generality** — 1 run/cell ⇒ directional signals, not significant effects (no CIs/p-values).
- **Clean per-component attribution** — cumulative configs confound; a C2 delta = "gate + whatever C1 changed" without targeted rollback cases.
- **Cross-model/domain generality** — deepseek-v4-flash on memory-architecture domain only; Claude subset (if run) is ranking-agreement external validity, reported beside not pooled.
- **Scored architecture tasks** — until GJ passes, they're explicitly unscored.

## Pragmatic cut (hit a meaningful MVP-2 without 80 runs)

- **Scored core** = 2 existing deterministic tasks + 1 deterministic stressing task (+ optionally permission-bait + coverage-breadth fixtures), each ×4 configs ≈ 12–20 runs, all scored WITHOUT a judge → real C0–C3 deltas.
- **Labeled remainder** = architecture-01/02 + unbuilt tasks shown with explicit judge_required/judge_pending labels (excluded from denominator). Satisfies the "20 tasks OR explicit status labels" clause.
- Defer the GJ judge gate (step 7) and the AiHubMix 32-run subset (replace with an explicit provider-gate-pending record).
- One recovery test, not three. One run per cell; repeats only on a stratified subset after the core completes.
- **Non-negotiable that survives every cut: step 1.** Even the smallest meaningful MVP-2 needs ONE task where the trigger fires.

## First piece to build

**Step 1, now.** It is the keystone dependency for everything measurable, it scores with NO judge (keeps the judge off the critical path), and it reuses the working snapshot-freeze + benchmark-task pipeline (low-risk plumbing, not new architecture). See `docs/mvp2-step1-stressing-task.md`.

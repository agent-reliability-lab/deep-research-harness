# MVP-2 Step · 干净 C0 vs C1 双跑评测协议

> 这是真实 ablation gate。anchor 接口已合并(`2d9eabf`),前一次 C0/C1 双方 grounding 失败的根因(literal `\n` excerpt 拒绝)应已修复。
> 这次跑要拿到三维 delta(EGTSR / cost / peak),并验证那条反直觉 finding "context saved ≠ cost saved" 在干净 baseline 下是否仍然成立。

## 1. 不变量(C0 vs C1 必须严格相同,否则 delta 污染)

- **同任务**:`memory-architecture-survey-01`(frozen, 18 claims, 17 sources)
- **同 snapshot**:`memory-architecture-mvp2-2026-06-22-v1`
- **同 budget**(见 §3)
- **同模型 + 同 temperature**:`deepseek-v4-flash` @ `temperature=0`
- **唯一变量**:configuration(C0=ReAct baseline / C1=+ compaction)

## 2. 跑之前的 pre-flight checks(失败任一停)

- [ ] `git status` 干净(main 上,无未提交改动) — 跑出来 trace 才能复现
- [ ] `python -m unittest discover -s tests -q` → OK
- [ ] `DEEPSEEK_API_KEY` 在 `.env`,model 是 `deepseek-v4-flash`
- [ ] C0 配置文件(默认)+ C1 配置文件(`configs/compaction.yaml`)都存在
- [ ] survey 任务 `lifecycle: frozen`、snapshot 匹配 — 已确认

## 3. Budget 参数(C0 / C1 同套)

基于上次 5 次 run 实测复算:

| 参数 | 值 | 依据 |
|---|---|---|
| `--max-active-context-tokens` | **100,000** | 上次实测 C0 peak ~66k、C1 peak ~85k;100k 留出 compaction 60% trigger = 60k 触发线 |
| `--max-uncached-input-tokens` | **150,000** | C1 实测 uncached ~102k(改写历史破缓存);150k 留 ~50k 缓冲,不要 100k 否则 C1 撞 cap 提前死 |
| `--max-input-tokens` | **(留空,不传)** | legacy raw cap;CLI 默认 `None` = **不启用**。若手动设 100k,缓存命中也算,会误杀 C1 |
| `--max-tool-calls-per-turn` | **4** | 防并行 JSON 撞 8192 输出上限(已上线) |
| `--max-iterations` | **25** | 默认 10 不够,survey 任务 ~10-15 模型调用 |
| `--max-model-calls` | **25** | 同上 |
| `--max-tool-calls` | **100** | survey 任务 ~36 次工具(17 read + 18 record + search + finalize);上次截断在第 67 次,留余量 |
| `--max-output-tokens` | **20,000** | 默认值;C0 单条 JSON 输出别撞 8192 |
| `--max-cost-usd` | **2** | 上次实测 C1 单跑 ~$0.023,C0 ~$0.011;cap 2 美元绝够,但拦失控 |
| `--max-duration-ms` | **1,200,000**(20min) | 默认 10min 不够,survey 任务跑 ~5-15min |

## 4. 复现命令(精确,可直接 paste)

```bash
PRICING=configs/pricing/deepseek-v4-flash-2026-06-22.json

# C0(baseline,无 compaction)
python -m src.agent.cli deepseek \
  --task data/tasks/frozen/memory-architecture-survey-01.json \
  --manifest data/source_snapshots/memory-architecture-mvp2-2026-06-22-v1/manifest.json \
  --pricing-file "$PRICING" \
  --configuration C0 \
  --run-group-id mvp2-c0-vs-c1-clean \
  --output "results/local/c0-clean-survey-$(date -u +%Y%m%dT%H%M%SZ)" \
  --max-iterations 25 \
  --max-model-calls 25 \
  --max-tool-calls 100 \
  --max-tool-calls-per-turn 4 \
  --max-active-context-tokens 100000 \
  --max-uncached-input-tokens 150000 \
  --max-output-tokens 20000 \
  --max-cost-usd 2 \
  --max-duration-ms 1200000

# C1(+ compaction Form A,trigger 60%)
python -m src.agent.cli deepseek \
  --task data/tasks/frozen/memory-architecture-survey-01.json \
  --manifest data/source_snapshots/memory-architecture-mvp2-2026-06-22-v1/manifest.json \
  --pricing-file "$PRICING" \
  --configuration C1 \
  --compaction-config configs/compaction.yaml \
  --run-group-id mvp2-c0-vs-c1-clean \
  --output "results/local/c1-clean-survey-$(date -u +%Y%m%dT%H%M%SZ)" \
  --max-iterations 25 \
  --max-model-calls 25 \
  --max-tool-calls 100 \
  --max-tool-calls-per-turn 4 \
  --max-active-context-tokens 100000 \
  --max-uncached-input-tokens 150000 \
  --max-output-tokens 20000 \
  --max-cost-usd 2 \
  --max-duration-ms 1200000
```

⚠️ **`--output`**(不是 `--output-dir`); **`--pricing-file`** 必填。legacy **`--max-input-tokens` 不传** — CLI 默认 `None`,不启用 raw cap;手动设 100k 会跟上次一样误杀 C1。

## 5. 跑完每个,立刻看 3 件事(在 trace.jsonl 里)

```bash
# 用 jq 或 python 速查
TRACE=results/local/c0-clean-survey-*/trace.jsonl  # 或 c1-clean-...

# (a) 是否撞 cap (status=budget_exhausted)?哪个 cap?
grep -o '"status":"[^"]*"\|"limit_name":"[^"]*"' $TRACE | sort -u

# (b) peak_active_context_tokens(EvaluationEvent 里)
grep -o '"peak_active_context_tokens":[0-9]*' $TRACE | tail -1

# (c) EGTSR 三项(EvaluationEvent)
grep -o '"required_claim_coverage":[0-9.]*\|"citation_precision":[0-9.]*\|"citation_entailment_rate":[0-9.]*\|"task_success":[a-z]*' $TRACE | tail -8

# (d) Anchor 失败率(grounding 是否真修了)
grep '"tool_name":"record_evidence"' $TRACE | grep -c '"status":"error"'
grep '"tool_name":"record_evidence"' $TRACE | grep -c '"status":"success"'
```

## 6. 验收(干净对照需满足的硬条件)

任一不满足,这次对照**不算干净** delta:

- [ ] **C0 + C1 都跑完**(`status=run_ended` 而非 budget exhausted)
- [ ] C0/C1 的 anchor 失败率 ≤ 5%(grounding 修复有效;>5% 说明 anchor prompt 还要调)
- [ ] C0 peak >= 60k(stressing task 真在压上下文,不然 C1 触发不了)
- [ ] C1 至少触发 **1 次 CompactionEvent**(否则等于 C0,无 ablation)
- [ ] C0 的 EGTSR 至少有一个 ≥ 0(可比;0 也行,但要看是 grounding 失败还是真不达标)

## 7. 四种可能结果(预设解读,防跑完模糊)

### 结果 A:C1 peak < C0 peak,且 C1 cost > C0 cost(反直觉 finding 重现)
→ 上次的 "context saved ≠ cost saved" finding 在干净 baseline 下**仍成立**。可以写进 README,作为 Lab 的 headline finding 兑现。下一步:Form B 设计。

### 结果 B:C1 peak < C0 peak,且 C1 cost ≈ C0 cost(Form A 不破 cache 了)
→ anchor 接口顺带改善了 cache pattern(因为 evidence 现在不靠 reload 源)。修正之前的判断,Form A 不是 net-negative。

### 结果 C:C1 peak ≈ C0 peak(rebound 仍存在)
→ Form A 反弹问题 anchor 接口没解。Form B 仍必要。需要在 trace 里找 rebound 发生时刻(是 record_evidence 完成后吗?还是 model_call 重读源?)

### 结果 D:C0 EGTSR > 0,C1 EGTSR = 0(compaction 损坏了任务完成)
→ critical fact retention 在真实 agent 输出下挂了。compaction 算法的"reloadable"假设不成立。

## 8. 我做完之后会给你什么

收到 C0 + C1 两份 trace + processed metrics,我会:
1. 算三维 delta 表(peak / cost / EGTSR / cache hit rate)
2. 判读是 A/B/C/D 中哪个结果
3. 起草 README "Status & findings" 节的更新(诚实标真实结果)
4. 决定下一步是 Form B 还是先调 C0

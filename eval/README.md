# BrainBench-lite — retrieval eval

The quality gate + sales artifact for BrunOS memory search (retrieval-v2 / C1). It
runs a labelled query set through `memory_search.search()` across three graph
configs and reports **file-level** P@5 / Recall@k / MRR.

```bash
uv run python eval/eval.py            # k=10 (default)
uv run python eval/eval.py --k 20
uv run python eval/eval.py --queries eval/queries.jsonl
```

> Run `memory_index.py --full` first if the vault changed — graph edges are only
> rebuilt by a full pass, and stale chunks skew the numbers.

## The label set (`queries.jsonl`)

JSONL, one `{"query": ..., "gold": [paths]}` per line. Paths are vault-relative
(under `BrunOS/Memory/`). **34 queries — 23 single-doc, 11 multi-doc.** Every gold
set is **hand-verified** against vault content (not auto-seeded, not set to whatever
the retriever happens to return — that would make the eval circular).

The 11 multi-doc queries are deliberate **"synthesize across linked notes"** tests
over the vault's two densest wikilink clusters (Brain/BaaS, Vertik) plus a
cross-folder *contrast* case (Memorial Colinas — relevant docs that are **not**
wikilinked, so graph can't help by construction). These are where graph traversal
*should* pay off, so they're the honest test of whether it does.

## Headline numbers (shipped config)

The shipped default is **graph ON, inject-only** — and it is identical to OFF at the
file level (see below), so these are the production numbers:

| metric | value |
|---|---|
| MRR | **0.836** (avg first-gold rank ≈ 1.2) |
| Recall@10 | **0.863** |
| Recall@20 | 0.904 |
| P@5 | 0.259 — *see interpretation* |

**Interpreting P@5.** P@5 = (gold docs in top-5) / 5. A single-doc query can score
at most 0.2 (1 / 5), so with 23 of 34 queries single-doc, P@5 is **structurally
deflated** and is *not* a meaningful headline for this mixed set. **MRR** (how high
the first right answer ranks) and **Recall@k** (how much of the gold is found) are
the figures to cite. Use a same-cardinality slice if you ever want a clean P@5.

## The graph re-decision (2026-06-07)

Run across all three configs, the data is unambiguous:

```
  metric             OFF    inject    rerank   Δrerank
  P@5              0.259     0.259     0.259    +0.000   (k=10)
  Recall@10        0.863     0.863     0.870    +0.007
  MRR              0.836     0.836     0.792    -0.044
```

**1. inject-only ≡ OFF — provably, not coincidentally.** An injected neighbor's
score is `GRAPH_BETA × (best connecting-seed chunk score) ≈ 0.05 × 0.016 ≈ 0.0008`,
far below the fused pool's floor (`≈ 1/(60+30) ≈ 0.011`). `graph_augment` returns the
top-k *by score*, so injected chunks always truncate out below any practical k. The
Δ = +0.000 across every metric at k=10 **and** k=20 confirms the math. The shipped
"graph ON" surfaces nothing the user sees — it is a no-op at the output.

**2. Do NOT flip `BRUNOS_GRAPH_RERANK=1` on.** Rerank buys a marginal recall gain
(+0.007 @k10, +0.022 @k20) at a real **MRR cost** (−0.044 @k10, −0.029 @k20): it
lifts linked siblings but demotes the precise answer (e.g. it pushes `vertik.md`
above the exact `orcamento_grounding_debug.md`). On a near-ceiling baseline where MRR
is the money metric, that's a losing trade. Keep the default.

**3. The reranker (`86ca1z88d`) — reframed by this evidence.** Rerank *reordering*
already measurably hurts, so a reorder-the-top-k reranker is not justified. The real,
measured gap is **recall of linked-but-unretrieved siblings**: Recall@10 tops out at
0.863, and the ~14% that never surfaces is exactly the linked siblings graph is
*supposed* to rescue (e.g. architecture `01/02/03` linked from a retrieved `README`,
or the Portuguese `vertik_prod_debug.md` linked from the retrieved orcamento notes)
— inject can't lift them because its score is below the cut. So the next retrieval-v2
investment should target **candidate-pool recall**, not reordering:

- **Cheapest high-value fix first:** make inject competitive — normalize RRF scores
  before fusing, or raise `GRAPH_BETA` enough that a strongly-linked sibling can
  actually enter top-k. This is the one change that would turn graph from a no-op
  into a recall lever, and it's testable right here.
- Only then consider a learned cross-encoder reranker over the (now graph-expanded)
  candidate pool.

## Known harness caveats (tracked separately)

- **`_inbox/sessions/` captures are indexed** and pollute Vertik queries (ephemeral
  raw captures out-rank curated docs; they're also retired over time, so they're
  never used as gold). Excluding `_inbox/` from the index would lift Vertik recall
  and stabilize the benchmark.
- **FTS5 lexical arm silently dies** on queries containing `-`/`'` (`go-to-market`,
  `brain-as-a-service`, `follow-ups`, `add-ons`, `Bruno's`): `keyword_search` passes
  the raw string to `MATCH`, FTS5 parse-errors, and the `except` returns `[]` → those
  queries run vector-only, understating the absolute numbers. The fix must preserve
  the intentional `+`/`-`/`"phrase"` operator support the memory-search skill
  documents.

Both are real but out of scope for the labels task; they don't affect the *relative*
OFF/inject/rerank comparison (all three share the same retrieval base).

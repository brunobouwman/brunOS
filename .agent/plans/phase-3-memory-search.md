# Feature: Phase 3 — Memory Search (Hybrid RAG)

The following plan should be complete, but it's important to validate Phase 2's `shared.py` shape and the installed library APIs before starting implementation. Pay special attention to:

- **Phase 2 dependency.** `shared.py` is being built in parallel by another session. This plan blocks on `shared.vault_path()` (and to a lesser extent `shared.atomic_write`, `shared.now_brt`). The implementation agent MUST verify those exist before writing `memory_index.py` — otherwise import errors. If Phase 2 hasn't merged when Phase 3 execution begins, STOP and surface to Bruno.
- **No Agent SDK calls in this phase.** The PRD's Phase 3 (lines 177–211) contains zero references to `claude_agent_sdk`. Indexing is deterministic chunking + embedding; search is deterministic ranking + fusion. Do **not** set `CLAUDE_INVOKED_BY` anywhere — including it would falsely signal "this is an SDK-invoked context" to Phase 8's hooks once they exist.
- **Asymmetric BGE.** `fastembed 0.8.x` (installed) ships an asymmetric API: `passage_embed()` for indexing, `query_embed()` for retrieval. BGE-small was trained with this asymmetry — use both methods in the right places. Calling generic `.embed()` for both sides degrades retrieval ~5–10% per the BGE model card.
- **sqlite-vec is pre-1.0.** Verified working with `MATCH ? AND k = ?` syntax (probed against installed 0.1.9, 2026-05-02). Schema is dim-agnostic via `vec0(embedding float[384])`. WHERE-clause filtering on joined columns has shifting behavior between point releases — over-fetch and post-filter for `--path-prefix`.
- **Security boundary.** `BrunOS/Memory/personal/finance.md` is OFF-LIMITS per SOUL.md and CLAUDE.md. The indexer MUST exclude `personal/finance.md` from the walk. (Other `personal/*.md` are in scope.)
- **Vault is read-only in Phase 3.** No script in this phase writes to `BrunOS/Memory/`. The DB lives at `.claude/data/state/memory.db`.
- **Decisions locked in conversation 2026-05-02:** RRF k=60 over weighted normalization; no custom `--path-prefix` targets beyond `drafts/sent` worth optimizing for; Postgres backend defined in interface but stub-raised until Phase 9.

## Feature Description

Phase 3 builds the hybrid (vector + keyword) search layer over `BrunOS/Memory/`. Output: four scripts under `.claude/scripts/`:

1. **`embeddings.py`** — singleton FastEmbed wrapper (`BAAI/bge-small-en-v1.5`, 384-dim).
2. **`db.py`** — backend abstraction (SQLite + sqlite-vec + FTS5 on Mac; Postgres + pgvector raises `NotImplementedError` until Phase 9).
3. **`memory_index.py`** — incremental walk of `Memory/**/*.md`, mtime-keyed, chunk-and-embed pipeline.
4. **`memory_search.py`** — hybrid retrieval: vector top-k×3 + FTS top-k×3 → RRF fusion → top-k JSON.

The primary downstream consumer is **Phase 6's draft generation** (voice-matching against `drafts/sent/`) and **Phase 7's chat bot** (context retrieval). The Phase 5 `news-digest` skill also uses search for deduplication. Phase 6's heartbeat is the indexing trigger — it calls `memory_index.py` at the start of each tick — but Phase 3 ships only the standalone CLI; no scheduling here.

## User Story

As Bruno (operator of BrunOS, with a vault that grows daily via heartbeat appends and drafts captures)
I want a single CLI/programmatic call that returns the top-k vault chunks matching a query, blending semantic similarity (vector) and exact-token recall (FTS), filterable by path prefix
So that Phase 6 can generate voice-matched drafts by retrieving prior `drafts/sent/` examples, Phase 7's chat bot can answer "what did I decide about X?" with vault evidence, and the news-digest skill can dedupe items against past digests.

## Problem Statement

The vault is plain markdown. Obsidian renders it; no structured retrieval exists. Without it:

1. Phase 6's draft generator has no way to retrieve "how did Bruno reply to similar messages last month?" — voice-matching fails, drafts feel generic.
2. Phase 7's chat bot can't ground answers in vault content beyond whatever the SessionStart hook eagerly injects.
3. The agent re-suggests ideas already captured in `projects/` because dedup is impossible.

A pure-keyword search misses paraphrased queries ("how do I prevent token leakage?" against a chunk titled "secret protection patterns"). A pure-vector search misses exact-token recalls (project codename "vertik" must rank top, even if surrounding sentences are off-topic). Hybrid retrieval with rank fusion is the standard fix.

## Solution Statement

Build the four files. SQLite + sqlite-vec + FTS5 on Mac (proven primitives, zero-config); Postgres + pgvector path defined in the same `db.py` interface but stub-raised until Phase 9. FastEmbed BGE-small for embeddings (no torch dep, ~130 MB ONNX, beats MiniLM on MTEB at the same dim). Chunking uses BGE's own tokenizer for accurate ~400-token windows with 50-token overlap, sliced on character offsets so original whitespace is preserved.

Indexing is incremental: track `mtime` per file in the chunks table; skip unchanged files. Deletion is detected by walking the on-disk file set and removing chunks for files no longer present. The CLI is the only entrypoint in Phase 3 — Phase 6's heartbeat will call it programmatically once it ships.

Search embeds the query with `query_embed()` (BGE asymmetric), runs vector top-3k and FTS top-3k, fuses via RRF (k=60), returns top-k JSON.

## Feature Metadata

**Feature Type**: New Capability (foundational retrieval)
**Estimated Complexity**: Medium
**Primary Systems Affected**: `.claude/scripts/` (4 new files), `.claude/data/state/memory.db` (created on first index), `.claude/data/fastembed_cache/` (populated on first embedding call), `CLAUDE.md` (commands appended)
**Dependencies**:
- Phase 0: deps installed, env vars set (verified 2026-05-02).
- **Phase 2: `shared.py` MUST land first** — provides `vault_path()`, `atomic_write()`, `now_brt()`. Block start of this phase on Phase 2 merge to main.
- External: fastembed 0.8.x (installed), sqlite-vec 0.1.x (installed 0.1.9), tokenizers (transitive via fastembed), numpy 2.x (installed 2.4.4), Python 3.10+ with `enable_load_extension` support (Bruno's 3.13.3 verified).

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: READ THESE BEFORE IMPLEMENTING

- `.agent/plans/second-brain-prd.md` (lines 177–211) — Why: source of truth for module names, embedding model, DB backends, fusion weights. PRD says "0.7 vector + 0.3 keyword (RRF or weighted normalization)" — we picked RRF k=60; see NOTES for rationale.
- `.agent/plans/second-brain-prd.md` (lines 39–58, Phase 0) — Why: confirms what's installed (fastembed, sqlite-vec, numpy, dotenv) and the `.claude/data/fastembed_cache/` cache convention.
- `.agent/plans/second-brain-prd.md` (lines 116–173, Phase 2) — Why: defines the `shared.py` API surface this phase consumes — `vault_path()` (deferred from Phase 0), `atomic_write`, `now_brt`. Confirm signatures match before importing; if Phase 2 deviates, surface to Bruno.
- `.agent/plans/second-brain-prd.md` (lines 353–442, Phase 6) — Why: primary consumer. Heartbeat calls `memory_index.py` at tick start and passes `--path-prefix drafts/sent` to `memory_search.py` for voice retrieval. Plan accordingly: search must accept `--path-prefix` and indexing must be cheap to re-run on each tick.
- `.agent/plans/phase-0-foundation-prep.md` (entire file) — Why: structural template for this plan. Validation-first style, idempotent operations, Bruno-asks-before-commit.
- `CLAUDE.md` (entire file) — Why: project conventions. Recursion-guard pattern is NOT applied here (no SDK calls); `setting_sources` policy is NOT applied here. Forbidden paths matter: `personal/finance.md` excluded from indexing.
- `requirements.txt` — Why: confirms `fastembed>=0.8,<0.9`, `sqlite-vec>=0.1.6,<0.2`, `numpy>=1.26,<3`. Pin these in mind when consulting Phase 3 docs (older fastembed had different APIs and method names).
- `BrunOS/Memory/_README.md` — Why: vault layout authority. Tells which subfolders are populated vs empty (`_README.md` markers). The indexer should index everything under `Memory/` except `personal/finance.md`.

### Existing State (verified 2026-05-02) — DO NOT REGENERATE

- `.claude/scripts/__init__.py` — empty package marker (Phase 0). DO NOT touch.
- `.claude/scripts/integrations/__init__.py` — empty (Phase 0). DO NOT touch.
- `.claude/data/fastembed_cache/.gitkeep` — Phase 0 placeholder. FastEmbed will populate this on first embedding call (~130 MB ONNX model + tokenizer JSON).
- `.claude/data/state/.gitkeep` — Phase 0 placeholder. The SQLite DB lands here as `memory.db`.
- `BrunOS/Memory/` — 34 markdown files across `daily/`, `projects/`, `team/`, `goals/`, `personal/`, `drafts/` (subfolders only have `_README.md` markers), plus 7 top-level singletons (`SOUL.md`, `USER.md`, `MEMORY.md`, `HEARTBEAT.md`, `HABITS.md`, `_README.md`, `sources_of_truth.md`). Total < 100 KB. Indexing will be sub-second after model warmup.
- `.venv/` — Python 3.13.3 with all Phase 0 deps installed and verified.

**Phase 2 deliverables (NOT YET LANDED at plan-write time):**
- `.claude/scripts/shared.py` — needed for `vault_path()`. **This plan blocks on Phase 2 merge.**
- `.claude/hooks/*` — irrelevant to Phase 3 directly; hooks don't call search/index.

### New Files to Create

All paths relative to repo root.

- `.claude/scripts/embeddings.py` — FastEmbed singleton + `embed_passages(texts)` and `embed_query(text)` helpers. ~50 lines.
- `.claude/scripts/db.py` — SQLite backend + Postgres stub. Public API: `connect()`, `init_schema()`, `upsert_chunk(...)`, `delete_chunks_for_file(...)`, `vector_search(...)`, `keyword_search(...)`, `all_file_mtimes()`, `get_chunks(ids)`. ~200 lines.
- `.claude/scripts/memory_index.py` — CLI + module. Walks `Memory/**/*.md` (excludes `personal/finance.md`), chunks via offset-preserving tokenizer, embeds in batches, upserts. Detects deletions. Flags: `--full`, `--paths <files>`, `--dry-run`. ~150 lines.
- `.claude/scripts/memory_search.py` — CLI + module. Embeds query, runs both retrievers, fuses via RRF, prints JSON. Flags: `<query>`, `--k`, `--path-prefix`. ~100 lines.

### Runtime Files Created on First Run (gitignored)

- `.claude/data/state/memory.db` — SQLite DB. Schema described in db.py task below.
- `.claude/data/fastembed_cache/<model-files>` — ONNX model + tokenizer JSON.

### Relevant Documentation — READ BEFORE IMPLEMENTING

- [FastEmbed on PyPI](https://pypi.org/project/fastembed/) — Why: confirm 0.8.x API. `TextEmbedding(model_name=..., cache_dir=...)`; `.passage_embed(iterable)` and `.query_embed(iterable)` return GENERATORS of `np.ndarray`. `.embedding_size` is the model dim. **Asymmetric calls matter for BGE — DO NOT call `.embed()` for both sides.**
- [BGE-small model card](https://huggingface.co/BAAI/bge-small-en-v1.5) — Why: query/passage prefix expectation. FastEmbed's `query_embed`/`passage_embed` apply the prefixes automatically; calling `.embed()` skips them and degrades retrieval.
- [sqlite-vec docs (Alex Garcia)](https://alexgarcia.xyz/sqlite-vec/) — Why: vec0 virtual table syntax, MATCH operator, k constraint inside WHERE.
- [sqlite-vec Python loader](https://github.com/asg017/sqlite-vec/blob/main/python/sqlite_vec/__init__.py) — Why: `sqlite_vec.load(conn)` requires `conn.enable_load_extension(True)` first. Apple-bundled stock sqlite3 does NOT support extension loading; python.org / Homebrew Python does. Bruno's setup verified 2026-05-02.
- [SQLite FTS5 docs](https://www.sqlite.org/fts5.html) — Why: contentless tables (`content=`, `content_rowid=`); trigger pattern for keeping FTS in sync; `bm25(table)` ranking function (returns NEGATIVE — lower is more relevant).
- [HuggingFace `tokenizers` Quicktour](https://huggingface.co/docs/tokenizers/quicktour) — Why: `Tokenizer.from_pretrained("BAAI/bge-small-en-v1.5")`; `.encode(text).offsets` returns `(start_char, end_char)` per token — used for offset-preserving chunking. Verified working 2026-05-02.
- [Reciprocal Rank Fusion — Cormack et al. 2009](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) — Why: original RRF paper. Default k=60. Formula: `score(d) = Σ 1/(k + rank_in_ranker(d))`. Sort descending.

### Patterns to Follow

**File-naming**: `snake_case.py` for Python modules (matches Phase 0 + PRD).

**Module shape**: each script has both a `def main()` (CLI entrypoint via `if __name__ == "__main__": main()`) and importable functions. Phase 6 will import directly; Phase 3 ships the CLI for testing.

**Argument parsing**: stdlib `argparse.ArgumentParser`. No external CLI lib in `requirements.txt`.

**JSON output for search**: `json.dumps(results, ensure_ascii=False, indent=2)` so Brazilian Portuguese characters in chunks render readable in terminals (Bruno's `drafts/sent/` corpus will eventually include them).

**Logging**: print to stderr via `print(..., file=sys.stderr)`. No `logging` config — Phase 6's heartbeat captures stderr per integration.

**Vault path resolution**: `from shared import vault_path` then `vault_path() / "Memory"`. Never hardcode `BrunOS/Memory/`.

**Atomic state writes**: not needed in Phase 3 — sqlite handles its own atomicity; no JSON state files.

**Import pattern for sibling scripts**: at the top of each script:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
```
then `from shared import vault_path`. The `.claude` directory is not a Python package (it has no top-level `__init__.py` and the leading dot makes it an invalid package name), so cross-script imports use the sibling-on-sys.path pattern. Phase 2's `shared.py` will establish this convention; mirror it.

**No recursion guard**: Phase 3 makes ZERO Agent SDK calls. Do not set `CLAUDE_INVOKED_BY` anywhere.

**Pinning policy**: code uses libraries already pinned in `requirements.txt`. No new deps in this phase.

---

## IMPLEMENTATION PLAN

### Phase 1: Verify pre-existing state (no writes)

Confirm Phase 2's `shared.py` is merged and the deps work. ABORT if not.

### Phase 2: Build the embedding singleton

`embeddings.py`. First call downloads ~130 MB ONNX model to the cache dir.

### Phase 3: Build the DB backend

`db.py` with SQLite implementation behind a backend-dispatch shim. Postgres path raises `NotImplementedError`. Schema is created idempotently via `init_schema()`.

### Phase 4: Build the indexer

`memory_index.py`. Walks vault (excluding `personal/finance.md`), chunks files, batch-embeds, upserts. Handles deletions.

### Phase 5: Build the search

`memory_search.py`. Imports `embed_query` and the db module. Runs both retrievers, fuses via RRF, prints JSON.

### Phase 6: Validate end-to-end

Index the full vault, run a representative search, verify incremental re-index is a no-op when nothing changed, verify deletion detection.

### Phase 7: Update CLAUDE.md

Append the two new build-commands lines. Mark Phase 3 as `[x]`. Add the "Memory search (Phase 3)" reference section per PRD line 211.

---

## STEP-BY-STEP TASKS

Execute every task in order. Each task has a single executable validation. Run from `/Users/brunobouwman/Documents/claude-second-brain/` with `.venv` activated.

### VERIFY Phase 2 has landed

- **CHECK**: `.claude/scripts/shared.py` exists and exports `vault_path`, `atomic_write`, `now_brt`.
- **GOTCHA**: If Phase 2 hasn't merged, STOP and surface to Bruno: "Phase 3 blocks on Phase 2's `shared.py`. Wait for the parallel session to merge before proceeding."
- **VALIDATE**:
  ```bash
  source .venv/bin/activate && \
  test -f .claude/scripts/shared.py && \
  python -c "
  import sys; sys.path.insert(0, '.claude/scripts')
  from shared import vault_path, atomic_write, now_brt
  vp = vault_path()
  assert vp.is_dir(), f'vault_path() returned non-dir: {vp}'
  assert (vp / 'Memory').is_dir(), 'Memory/ subdir missing'
  print('shared.py OK:', vp)
  "
  ```

### VERIFY required deps importable

- **CHECK**: fastembed, sqlite_vec, tokenizers, numpy import; sqlite3 supports `enable_load_extension`.
- **VALIDATE**:
  ```bash
  source .venv/bin/activate && python -c "
  import fastembed, sqlite_vec, tokenizers, numpy, sqlite3
  conn = sqlite3.connect(':memory:')
  conn.enable_load_extension(True)
  print('deps OK; fastembed', fastembed.__version__, 'sqlite-vec', sqlite_vec.__version__)
  "
  ```

### CREATE `.claude/scripts/embeddings.py`

- **IMPLEMENT**: FastEmbed singleton wrapping BGE-small. Two helpers: `embed_passages(texts)` and `embed_query(text)`. Cache dir is `.claude/data/fastembed_cache/`.
- **PATTERN**: module-level lazy singleton (`_model = None`, init on first call).
- **CONTENT** (sketch — implementation agent finalizes):
  ```python
  from __future__ import annotations
  import sys
  from pathlib import Path
  from typing import Iterable, List

  sys.path.insert(0, str(Path(__file__).parent))

  import numpy as np
  from fastembed import TextEmbedding

  MODEL_NAME = "BAAI/bge-small-en-v1.5"
  EMBED_DIM = 384
  CACHE_DIR = Path(__file__).parent.parent / "data" / "fastembed_cache"

  _model: TextEmbedding | None = None

  def _get_model() -> TextEmbedding:
      global _model
      if _model is None:
          CACHE_DIR.mkdir(parents=True, exist_ok=True)
          _model = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(CACHE_DIR))
      return _model

  def embed_passages(texts: Iterable[str]) -> List[np.ndarray]:
      m = _get_model()
      return list(m.passage_embed(list(texts)))

  def embed_query(text: str) -> np.ndarray:
      m = _get_model()
      return next(iter(m.query_embed([text])))
  ```
- **GOTCHA**: `TextEmbedding.passage_embed()` and `query_embed()` return GENERATORS, not lists. Wrap with `list(...)`.
- **GOTCHA**: BGE expects asymmetric prefixes (passage vs query). FastEmbed applies them automatically when you use the right method. Calling `.embed()` for both sides degrades retrieval ~5–10%.
- **GOTCHA**: First call downloads ~130 MB. On a slow connection this can take 1–3 min. Don't time out validation at < 5 min.
- **VALIDATE**:
  ```bash
  source .venv/bin/activate && python -c "
  import sys; sys.path.insert(0, '.claude/scripts')
  from embeddings import embed_passages, embed_query, EMBED_DIM
  p = embed_passages(['hello world', 'second passage'])
  q = embed_query('hello?')
  assert len(p) == 2 and p[0].shape == (EMBED_DIM,), f'passage shape: {p[0].shape}'
  assert q.shape == (EMBED_DIM,), f'query shape: {q.shape}'
  print('embeddings OK, dim=', EMBED_DIM)
  "
  ```

### CREATE `.claude/scripts/db.py`

- **IMPLEMENT**: Backend-dispatch shim with SQLite implementation. Postgres path raises `NotImplementedError`.
- **SCHEMA** (created idempotently in `init_schema()`):
  ```sql
  CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    chunk_idx INTEGER NOT NULL,
    content TEXT NOT NULL,
    mtime REAL NOT NULL,
    UNIQUE(file_path, chunk_idx)
  );
  CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(file_path);

  CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0(
    embedding float[384]
  );

  CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    content,
    content='chunks',
    content_rowid='id',
    tokenize='porter unicode61'
  );

  CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunk_fts(rowid, content) VALUES (new.id, new.content);
  END;

  CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunk_fts(chunk_fts, rowid, content) VALUES('delete', old.id, old.content);
  END;
  ```
- **PUBLIC API**:
  ```python
  def connect() -> sqlite3.Connection
  def init_schema(conn) -> None
  def upsert_chunk(conn, file_path, chunk_idx, content, mtime, embedding) -> int
  def delete_chunks_for_file(conn, file_path) -> int
  def vector_search(conn, qemb, k, path_prefix=None) -> list[dict]
  def keyword_search(conn, query, k, path_prefix=None) -> list[dict]
  def all_file_mtimes(conn) -> dict[str, float]
  def get_chunks(conn, ids) -> dict[int, dict]
  ```
- **CONNECT IMPL**:
  ```python
  import os, sqlite3, sqlite_vec, numpy as np
  from pathlib import Path

  DB_PATH = Path(__file__).parent.parent / "data" / "state" / "memory.db"

  def connect() -> sqlite3.Connection:
      backend = os.environ.get("DB_BACKEND", "sqlite")
      if backend != "sqlite":
          raise NotImplementedError(f"DB_BACKEND={backend} ships in Phase 9")
      DB_PATH.parent.mkdir(parents=True, exist_ok=True)
      conn = sqlite3.connect(str(DB_PATH))
      conn.enable_load_extension(True)
      sqlite_vec.load(conn)
      conn.enable_load_extension(False)
      conn.row_factory = sqlite3.Row
      return conn
  ```
- **UPSERT IMPL**:
  ```python
  def upsert_chunk(conn, file_path, chunk_idx, content, mtime, embedding):
      blob = embedding.astype(np.float32).tobytes()
      cur = conn.execute(
          "INSERT INTO chunks(file_path, chunk_idx, content, mtime) VALUES (?, ?, ?, ?) "
          "ON CONFLICT(file_path, chunk_idx) DO UPDATE SET "
          "content=excluded.content, mtime=excluded.mtime "
          "RETURNING id",
          (file_path, chunk_idx, content, mtime))
      chunk_id = cur.fetchone()["id"]
      conn.execute("DELETE FROM chunk_vec WHERE rowid = ?", (chunk_id,))
      conn.execute("INSERT INTO chunk_vec(rowid, embedding) VALUES (?, ?)", (chunk_id, blob))
      return chunk_id
  ```
- **DELETE IMPL**: vec0 doesn't cascade. Look up chunk IDs first, delete from `chunk_vec`, then delete from `chunks` (which fires the FTS trigger):
  ```python
  def delete_chunks_for_file(conn, file_path):
      ids = [r["id"] for r in conn.execute("SELECT id FROM chunks WHERE file_path = ?", (file_path,))]
      if not ids:
          return 0
      conn.executemany("DELETE FROM chunk_vec WHERE rowid = ?", [(i,) for i in ids])
      conn.execute("DELETE FROM chunks WHERE file_path = ?", (file_path,))
      return len(ids)
  ```
- **VECTOR SEARCH IMPL**:
  ```python
  def vector_search(conn, qemb, k, path_prefix=None):
      qblob = qemb.astype(np.float32).tobytes()
      if path_prefix is None:
          rows = conn.execute(
              "SELECT v.rowid AS id, v.distance, c.file_path, c.chunk_idx, c.content "
              "FROM chunk_vec v JOIN chunks c ON c.id = v.rowid "
              "WHERE v.embedding MATCH ? AND k = ? "
              "ORDER BY v.distance",
              (qblob, k)).fetchall()
      else:
          inner_k = max(k * 5, 50)
          rows = conn.execute(
              "SELECT v.rowid AS id, v.distance, c.file_path, c.chunk_idx, c.content "
              "FROM chunk_vec v JOIN chunks c ON c.id = v.rowid "
              "WHERE v.embedding MATCH ? AND k = ? "
              "AND c.file_path LIKE ? || '/%' "
              "ORDER BY v.distance LIMIT ?",
              (qblob, inner_k, path_prefix, k)).fetchall()
      return [dict(r) for r in rows]
  ```
- **KEYWORD SEARCH IMPL**:
  ```python
  def keyword_search(conn, query, k, path_prefix=None):
      sql = ("SELECT c.id, c.file_path, c.chunk_idx, c.content, bm25(chunk_fts) AS score "
             "FROM chunk_fts JOIN chunks c ON c.id = chunk_fts.rowid "
             "WHERE chunk_fts MATCH ? ")
      params: list = [query]
      if path_prefix:
          sql += "AND c.file_path LIKE ? || '/%' "
          params.append(path_prefix)
      sql += "ORDER BY score LIMIT ?"
      params.append(k)
      try:
          return [dict(r) for r in conn.execute(sql, params).fetchall()]
      except sqlite3.OperationalError as e:
          print(f"FTS5 parse error: {e}; returning []", file=sys.stderr)
          return []
  ```
- **GOTCHA**: BM25 in FTS5 returns NEGATIVE scores — lower (more negative) = more relevant. Sort ASCENDING. RRF fusion uses RANK only, so this doesn't propagate.
- **GOTCHA**: FTS5 MATCH syntax has special tokens (AND, OR, NOT, NEAR, column filters, parens, quotes). Raw user queries with these can raise `OperationalError`. The try/except above falls back to empty results; the caller (`memory_search.py`) treats this as "FTS contributed nothing" and proceeds with vector-only.
- **GOTCHA**: vec0 + WHERE-on-joined-column behavior shifts between sqlite-vec point releases. The over-fetch pattern (`inner_k = max(k*5, 50)` with `LIMIT k` outside) is the safety net. If the JOIN+WHERE syntax raises, fall back to: query top inner_k with no filter, then post-filter in Python.
- **GOTCHA**: `path_prefix` matches directory boundaries via `LIKE 'drafts/sent/%'`. Do NOT use `LIKE 'drafts/sent%'` — that would match `drafts/sent_archive.md` if it existed.
- **GOTCHA**: numpy 2.x is installed (2.4.4). `tobytes()` on float32 arrays is stable across numpy 1.x → 2.x.
- **VALIDATE**:
  ```bash
  source .venv/bin/activate && rm -f .claude/data/state/memory.db && python -c "
  import sys, numpy as np
  sys.path.insert(0, '.claude/scripts')
  from db import connect, init_schema, upsert_chunk, vector_search, keyword_search, delete_chunks_for_file, all_file_mtimes
  conn = connect()
  init_schema(conn)
  e1 = np.random.RandomState(0).randn(384).astype(np.float32)
  e2 = np.random.RandomState(1).randn(384).astype(np.float32)
  upsert_chunk(conn, 'test/foo.md', 0, 'hello world test content', 1234.5, e1)
  upsert_chunk(conn, 'test/bar.md', 0, 'completely different topic about ferrets', 1234.5, e2)
  conn.commit()
  v = vector_search(conn, e1, k=2)
  k = keyword_search(conn, 'hello', k=2)
  assert len(v) == 2 and v[0]['file_path'] == 'test/foo.md', f'vec: {v}'
  assert any(r['file_path'] == 'test/foo.md' for r in k), f'fts: {k}'
  mtimes = all_file_mtimes(conn)
  assert mtimes.get('test/foo.md') == 1234.5
  delete_chunks_for_file(conn, 'test/foo.md')
  conn.commit()
  assert 'test/foo.md' not in all_file_mtimes(conn)
  conn.close()
  print('db.py OK')
  " && rm -f .claude/data/state/memory.db
  ```

### CREATE `.claude/scripts/memory_index.py`

- **IMPLEMENT**: Walk the vault, chunk via offset-preserving tokenizer, batch-embed, upsert. Detect deletions. Exclude `personal/finance.md`.
- **CHUNKING**:
  ```python
  from tokenizers import Tokenizer

  CHUNK_TOKENS = 400
  OVERLAP_TOKENS = 50
  STEP = CHUNK_TOKENS - OVERLAP_TOKENS

  _tok: Tokenizer | None = None

  def _get_tokenizer() -> Tokenizer:
      global _tok
      if _tok is None:
          _tok = Tokenizer.from_pretrained("BAAI/bge-small-en-v1.5")
      return _tok

  def chunk_text(text: str) -> list[str]:
      if not text.strip():
          return []
      enc = _get_tokenizer().encode(text)
      ids, offsets = enc.ids, enc.offsets
      if len(ids) <= CHUNK_TOKENS:
          return [text]
      chunks: list[str] = []
      i = 0
      while i < len(ids):
          end = min(i + CHUNK_TOKENS, len(ids))
          start_char = offsets[i][0]
          end_char = offsets[end - 1][1]
          chunks.append(text[start_char:end_char])
          if end == len(ids):
              break
          i += STEP
      return chunks
  ```
- **WALK + INDEX**:
  ```python
  EXCLUDE_RELATIVE = {"personal/finance.md"}

  def index(full=False, paths=None, dry_run=False) -> int:
      vault = vault_path() / "Memory"
      if paths:
          md_files = [Path(p).resolve() for p in paths]
      else:
          md_files = sorted(vault.glob("**/*.md"))

      conn = connect()
      init_schema(conn)
      indexed_mtimes = all_file_mtimes(conn)
      on_disk: set[str] = set()
      to_index: list[Path] = []

      for f in md_files:
          rel = f.relative_to(vault).as_posix()
          if rel in EXCLUDE_RELATIVE:
              continue
          on_disk.add(rel)
          cur_mtime = f.stat().st_mtime
          prev = indexed_mtimes.get(rel, -1.0)
          if not full and abs(prev - cur_mtime) < 1e-6:
              continue
          to_index.append(f)

      print(f"to_index: {len(to_index)} / {len(md_files)} files", file=sys.stderr)

      if not dry_run:
          for f in to_index:
              rel = f.relative_to(vault).as_posix()
              text = f.read_text(encoding="utf-8")
              chunks = chunk_text(text)
              if not chunks:
                  delete_chunks_for_file(conn, rel)
                  conn.commit()
                  continue
              embeddings = embed_passages(chunks)
              delete_chunks_for_file(conn, rel)
              cur_mtime = f.stat().st_mtime
              for i, (c, e) in enumerate(zip(chunks, embeddings)):
                  upsert_chunk(conn, rel, i, c, cur_mtime, e)
              conn.commit()
              print(f"  indexed {rel} ({len(chunks)} chunks)", file=sys.stderr)

          if not paths:
              stale = set(indexed_mtimes.keys()) - on_disk
              for rel in stale:
                  delete_chunks_for_file(conn, rel)
                  print(f"  deleted {rel}", file=sys.stderr)
              conn.commit()

      conn.close()
      return len(to_index)
  ```
- **CLI**:
  ```python
  def main():
      import argparse
      ap = argparse.ArgumentParser()
      ap.add_argument("--full", action="store_true")
      ap.add_argument("--paths", nargs="*")
      ap.add_argument("--dry-run", action="store_true")
      args = ap.parse_args()
      n = index(full=args.full, paths=args.paths, dry_run=args.dry_run)
      print(f"indexed {n} files", file=sys.stderr)

  if __name__ == "__main__":
      main()
  ```
- **GOTCHA**: Tokenizer first-load downloads tokenizer.json from HF (~1 MB). The `HF_TOKEN not set` warning is non-fatal.
- **GOTCHA**: `f.stat().st_mtime` is a float with sub-second resolution. Some filesystems round to integer seconds — use `abs(a - b) < 1e-6` tolerance, never raw equality.
- **GOTCHA**: Deletion sweep MUST be skipped when `--paths` is specified — otherwise all OTHER files' chunks get deleted because they're not in the requested list.
- **GOTCHA**: Empty markdown files (frontmatter-only or nothing) chunk to `[]`. Skip them entirely (no upsert) — they won't pollute search results, and they'll be picked up next walk if/when they get content.
- **GOTCHA**: `EXCLUDE_RELATIVE = {"personal/finance.md"}` — vault-relative paths only. The check is `rel in EXCLUDE_RELATIVE`. Never re-derive from the absolute path.
- **GOTCHA**: Don't strip frontmatter. The YAML block is part of the searchable surface — `tags: [vertik]` should match a query for "vertik".
- **VALIDATE (full index)**:
  ```bash
  source .venv/bin/activate && \
  rm -f .claude/data/state/memory.db && \
  python .claude/scripts/memory_index.py --full && \
  python -c "
  import sys; sys.path.insert(0, '.claude/scripts')
  from db import connect
  conn = connect()
  n_chunks = conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
  n_files = conn.execute('SELECT COUNT(DISTINCT file_path) FROM chunks').fetchone()[0]
  n_vec = conn.execute('SELECT COUNT(*) FROM chunk_vec').fetchone()[0]
  excluded = conn.execute(\"SELECT 1 FROM chunks WHERE file_path = 'personal/finance.md'\").fetchall()
  assert n_chunks == n_vec, f'chunks={n_chunks} vec={n_vec}'
  assert n_files >= 25, f'expected ≥25 files indexed, got {n_files}'
  assert not excluded, 'personal/finance.md indexed but should be excluded'
  print(f'indexed: {n_files} files / {n_chunks} chunks; finance.md excluded')
  "
  ```
- **VALIDATE (incremental no-op)**:
  ```bash
  OUT=$(python .claude/scripts/memory_index.py 2>&1)
  echo "$OUT" | grep -q "to_index: 0" && echo "incremental OK" || (echo "FAIL re-run did not skip"; echo "$OUT")
  ```

### CREATE `.claude/scripts/memory_search.py`

- **IMPLEMENT**: Embed query, run both retrievers (k×3 each), fuse via RRF (k=60), return top-k JSON.
- **CONTENT**:
  ```python
  from __future__ import annotations
  import argparse, json, sys
  from collections import defaultdict
  from pathlib import Path

  sys.path.insert(0, str(Path(__file__).parent))

  from db import connect, vector_search, keyword_search
  from embeddings import embed_query

  RRF_K = 60

  def rrf_fuse(rankings: list[list[dict]], top_k: int) -> list[dict]:
      scores: dict[int, float] = defaultdict(float)
      lookup: dict[int, dict] = {}
      for ranking in rankings:
          for rank, row in enumerate(ranking):
              cid = row["id"]
              scores[cid] += 1.0 / (RRF_K + rank + 1)
              if cid not in lookup:
                  lookup[cid] = row
      sorted_ids = sorted(scores.keys(), key=lambda c: -scores[c])[:top_k]
      out = []
      for cid in sorted_ids:
          row = dict(lookup[cid])
          row["score"] = scores[cid]
          out.append(row)
      return out

  def search(query: str, k: int = 10, path_prefix: str | None = None) -> list[dict]:
      conn = connect()
      try:
          qemb = embed_query(query)
          inner_k = max(k * 3, 30)
          vec = vector_search(conn, qemb, k=inner_k, path_prefix=path_prefix)
          kw = keyword_search(conn, query, k=inner_k, path_prefix=path_prefix)
      finally:
          conn.close()
      return rrf_fuse([vec, kw], top_k=k)

  def main():
      ap = argparse.ArgumentParser()
      ap.add_argument("query")
      ap.add_argument("--k", type=int, default=10)
      ap.add_argument("--path-prefix", default=None)
      args = ap.parse_args()
      results = search(args.query, k=args.k, path_prefix=args.path_prefix)
      print(json.dumps(results, ensure_ascii=False, indent=2))

  if __name__ == "__main__":
      main()
  ```
- **GOTCHA**: If both retrievers return zero rows (e.g., empty index, FTS parse error + index-cold-start), return `[]` — RRF over empty rankings naturally yields `[]`.
- **GOTCHA**: `--path-prefix drafts/sent` matches `drafts/sent/2026-04-30_email_alice.md`, NOT `drafts/sent_archive.md` — db.py enforces with trailing `/%`.
- **GOTCHA**: Numeric IDs from sqlite3.Row come back as ints; the `defaultdict[int, float]` typing is correct.
- **VALIDATE (positive case)**:
  ```bash
  source .venv/bin/activate && \
  python .claude/scripts/memory_search.py "Lisa freelance" --k 5 | python -c "
  import json, sys
  results = json.load(sys.stdin)
  assert len(results) > 0, 'no results for known-good query'
  paths = [r['file_path'] for r in results]
  print('top paths:', paths)
  assert any('lisa' in p.lower() for p in paths), f'lisa.md missing from top-5: {paths}'
  print('search OK')
  "
  ```
- **VALIDATE (path-prefix filter — drafts/sent is empty pre-Phase 6)**:
  ```bash
  python .claude/scripts/memory_search.py "anything" --k 5 --path-prefix drafts/sent | python -c "
  import json, sys
  results = json.load(sys.stdin)
  assert results == [], f'expected empty (drafts/sent unpopulated), got {len(results)} results'
  print('path-prefix filter OK')
  "
  ```
- **VALIDATE (FTS parse-resilience)**:
  ```bash
  python .claude/scripts/memory_search.py "what about (foo)?" --k 3 >/dev/null && echo "FTS-special-char query OK"
  ```

### UPDATE `CLAUDE.md`

- **IMPLEMENT**: Append two build-commands lines and a short reference section. Flip Phase 3 to `[x]` in the Phase status list.
- **APPEND to `## Build commands` section**:
  ```bash
  python .claude/scripts/memory_index.py [--full] [--paths file1.md file2.md] [--dry-run]
  python .claude/scripts/memory_search.py "<query>" [--k 10] [--path-prefix drafts/sent]
  ```
- **APPEND new section** (per PRD line 211):
  ```markdown
  ## Memory search (Phase 3)

  Embedding model: `BAAI/bge-small-en-v1.5` via FastEmbed (384-dim, asymmetric — `passage_embed` for indexing, `query_embed` for retrieval). Cache: `.claude/data/fastembed_cache/`. DB: `.claude/data/state/memory.db` (SQLite + sqlite-vec + FTS5; Postgres+pgvector path stubbed for Phase 9 VPS deploy). Hybrid retrieval merges vector top-k×3 + FTS top-k×3 via RRF (k=60). The indexer excludes `Memory/personal/finance.md` per the SOUL.md no-financial-data boundary.
  ```
- **GOTCHA**: Phase 2 is also appending to CLAUDE.md (memory_flush command + recursion-guard prose). If a 3-way merge conflict appears, keep BOTH phases' additions; they target different sections.
- **VALIDATE**:
  ```bash
  grep -q "memory_index.py" CLAUDE.md && \
  grep -q "memory_search.py" CLAUDE.md && \
  grep -q "BAAI/bge-small-en-v1.5" CLAUDE.md && \
  grep -q "\[x\] Phase 3" CLAUDE.md && \
  echo "CLAUDE.md updated"
  ```

### COMMIT (Bruno-controlled)

- **IMPLEMENT**: Surface to Bruno: "Phase 3 ready. Suggested commit: `feat: Phase 3 hybrid memory search (FastEmbed + sqlite-vec + FTS5 + RRF)`. Want me to commit or review first?"
- **GOTCHA**: NEVER auto-commit per global rules.

---

## TESTING STRATEGY

The project has no pytest config and the PRD doesn't mandate one. Inline validation per task is the test surface — each `### CREATE` task has a self-contained `python -c` validation that exercises the just-built module. Phase 3 does NOT add a test framework.

### Unit-style validation (per-task, inline)

Already specified in each task above. Validations are idempotent — they reset state where needed (e.g., `rm -f .claude/data/state/memory.db` before db.py validation).

### Integration validation

End-to-end search after a full index (run after the indexer task validates):

- Query `"Lisa freelance"` → expect `team/lisa.md` in top-5.
- Query `"vertik architecture"` → expect `projects/vertik_architecture.md` in top-3.
- Query `"AI engineering transition"` → expect at least one chunk from `MEMORY.md` or `goals/personal_vision.md` (both mention the transition).
- Query with FTS-special chars `"what about (foo)?"` → must not crash; FTS contributes empty, vector still returns results.

### Edge cases

- **Empty index, search runs**: returns `[]`, no crash. Probe by `rm memory.db` then running search.
- **Empty file in vault**: skipped during index (`chunks == []`). Probe: `touch BrunOS/Memory/empty_test.md`, index, confirm no chunks for it, then `rm` it. Re-index — deletion sweep handles the cleanup.
- **File renamed**: old chunks remain until next full walk. Acceptable — heartbeat triggers walks every tick (Phase 6), so staleness is bounded to 30 min.
- **File modified externally (Obsidian edit)**: mtime updates → next index run picks it up. Confirmed by the `--full` vs default-incremental behavior.
- **Large file > 4k tokens**: chunked with overlap. A synthetic 10k-token file should produce ≥25 chunks (10k/350 ≈ 29).
- **Query in Portuguese**: BGE-small handles it weakly but doesn't crash. Acceptable for v1; revisit in Phase 6 if voice-matching feels off.
- **FTS5 special chars in query**: try/except in `keyword_search` returns `[]` and logs to stderr; `memory_search.py` proceeds vector-only.
- **Concurrent index runs**: Phase 3 assumes only one indexer at a time. Phase 6 will add a process lock around heartbeat invocations if needed. SQLite default journal mode is sufficient for single-writer scenarios.
- **Vault path resolves to non-Memory dir**: `(vault_path() / "Memory").glob("**/*.md")` returns empty → walk is a no-op. Validation catches this when the indexed-files count is < 25.
- **`personal/finance.md` accidentally included**: db.py validation contains an explicit assertion `assert not excluded`.
- **Phase 2 ships `vault_path()` with different signature**: the verify-Phase-2 task at the top catches this before any code is written.

---

## VALIDATION COMMANDS

Run from `/Users/brunobouwman/Documents/claude-second-brain/` with `.venv` activated. Each level must pass before the next.

### Level 1: Pre-flight

```bash
source .venv/bin/activate
test -f .claude/scripts/shared.py && echo "shared.py present" || echo "ABORT: Phase 2 not merged"
python -c "import fastembed, sqlite_vec, tokenizers, numpy; print('deps OK')"
python -c "import sqlite3; c=sqlite3.connect(':memory:'); c.enable_load_extension(True); print('extensions OK')"
python -c "
import sys; sys.path.insert(0, '.claude/scripts')
from shared import vault_path
assert (vault_path()/'Memory').is_dir()
print('vault OK:', vault_path())
"
```

### Level 2: Files created

```bash
test -f .claude/scripts/embeddings.py && \
test -f .claude/scripts/db.py && \
test -f .claude/scripts/memory_index.py && \
test -f .claude/scripts/memory_search.py && \
echo "all Phase 3 deliverables present"
```

### Level 3: Modules import clean

```bash
source .venv/bin/activate
python -c "
import sys; sys.path.insert(0, '.claude/scripts')
import embeddings, db, memory_index, memory_search
print('imports OK')
"
```

### Level 4: Embeddings shape

```bash
source .venv/bin/activate
python -c "
import sys; sys.path.insert(0, '.claude/scripts')
from embeddings import embed_passages, embed_query, EMBED_DIM
assert embed_passages(['hi'])[0].shape == (EMBED_DIM,)
assert embed_query('hi').shape == (EMBED_DIM,)
print('embeddings OK')
"
```

### Level 5: DB schema + round-trip

```bash
source .venv/bin/activate
rm -f .claude/data/state/memory.db
python -c "
import sys, numpy as np
sys.path.insert(0, '.claude/scripts')
from db import connect, init_schema, upsert_chunk, vector_search, keyword_search, all_file_mtimes
conn = connect(); init_schema(conn)
e = np.random.RandomState(0).randn(384).astype(np.float32)
upsert_chunk(conn, 'test/foo.md', 0, 'hello world', 1.0, e)
conn.commit()
assert len(vector_search(conn, e, k=1)) == 1
assert len(keyword_search(conn, 'hello', k=1)) == 1
print('db OK')
"
rm -f .claude/data/state/memory.db
```

### Level 6: Full vault index + incremental no-op

```bash
source .venv/bin/activate
rm -f .claude/data/state/memory.db
python .claude/scripts/memory_index.py --full
OUT=$(python .claude/scripts/memory_index.py 2>&1)
echo "$OUT" | grep -q "to_index: 0" && echo "incremental OK"
```

### Level 7: End-to-end search

```bash
source .venv/bin/activate
python .claude/scripts/memory_search.py "Lisa freelance" --k 5 | python -c "
import json, sys
r = json.load(sys.stdin)
assert any('lisa' in x['file_path'].lower() for x in r), f'lisa missing: {[x[\"file_path\"] for x in r]}'
print('search OK')
"
python .claude/scripts/memory_search.py "what about (foo)?" --k 3 >/dev/null && echo "FTS-special-char OK"
python .claude/scripts/memory_search.py "x" --k 3 --path-prefix drafts/sent | python -c "
import json, sys
assert json.load(sys.stdin) == []
print('path-prefix OK')
"
```

### Level 8: Security boundary

```bash
source .venv/bin/activate
python -c "
import sys; sys.path.insert(0, '.claude/scripts')
from db import connect
conn = connect()
assert not conn.execute(\"SELECT 1 FROM chunks WHERE file_path='personal/finance.md'\").fetchall(), \
    'personal/finance.md was indexed despite exclusion'
print('finance.md correctly excluded')
"
```

### Level 9: CLAUDE.md updated

```bash
grep -q "memory_index.py" CLAUDE.md && \
grep -q "memory_search.py" CLAUDE.md && \
grep -q "BAAI/bge-small-en-v1.5" CLAUDE.md && \
grep -q "\[x\] Phase 3" CLAUDE.md && \
echo "CLAUDE.md OK"
```

### Level 10: Postgres backend stub

```bash
source .venv/bin/activate
DB_BACKEND=postgres python -c "
import sys; sys.path.insert(0, '.claude/scripts')
from db import connect
try:
    connect()
    print('FAIL: postgres backend should raise')
except NotImplementedError as e:
    print('postgres stub OK:', e)
"
```

---

## ACCEPTANCE CRITERIA

- [ ] All four scripts created (`embeddings.py`, `db.py`, `memory_index.py`, `memory_search.py`).
- [ ] `python .claude/scripts/memory_index.py --full` indexes ≥25 files from `BrunOS/Memory/` without errors.
- [ ] `personal/finance.md` is NOT indexed (security boundary).
- [ ] Re-running `memory_index.py` (no `--full`) reports `to_index: 0` (incremental works).
- [ ] `memory_search.py "Lisa freelance" --k 5` returns at least one chunk from `team/lisa.md`.
- [ ] `memory_search.py "x" --path-prefix drafts/sent` returns `[]` (filter scopes correctly; folder is empty pre-Phase 6).
- [ ] FTS-special-char queries (`"what about (foo)?"`) do not crash; vector results are still returned.
- [ ] DB at `.claude/data/state/memory.db` exists with matching counts: `chunks` row count == `chunk_vec` row count.
- [ ] FastEmbed cache populated at `.claude/data/fastembed_cache/`.
- [ ] No new entries in `requirements.txt`.
- [ ] `CLAUDE.md` flips Phase 3 to `[x]` and appends the build-commands + reference section.
- [ ] `BrunOS/Memory/` files are read-only — Phase 3 does not write to the vault.
- [ ] `DB_BACKEND=postgres` raises `NotImplementedError` cleanly.
- [ ] No regressions in Phase 0 / Phase 2 deliverables (`shared.py`, hooks, settings.json untouched by Phase 3).

---

## COMPLETION CHECKLIST

- [ ] Phase 2's `shared.py` confirmed present and exports `vault_path` before Phase 3 code is written.
- [ ] All tasks executed top-to-bottom in order.
- [ ] Each task's inline validation passed.
- [ ] Levels 1–10 of VALIDATION COMMANDS all pass.
- [ ] Manual sanity check: try a few queries from Bruno's actual recent vault content (`vertik`, `protostack`, daily-log themes).
- [ ] CLAUDE.md updated and reviewed for accuracy.
- [ ] Bruno asked before committing.
- [ ] Phase 3 mark in CLAUDE.md `Phase status` is `[x]`.

---

## NOTES

### Why RRF over weighted normalization

The PRD allows either: "merge with 0.7 vector + 0.3 keyword (RRF or weighted normalization)" (line 202). RRF is the better default because:

1. **Score-scale invariance**: cosine distance is in [0, 2]; FTS5 BM25 is unbounded negative. Weighted sum requires per-call normalization (min-max or z-score), which is brittle when one ranker returns very few rows.
2. **Industry standard**: RRF is the default in Vespa, Elasticsearch hybrid retriever, and the original Cormack 2009 paper. k=60 is the universally cited default.
3. **No tuning**: weighted normalization invites hyperparameter drift (0.7 vs 0.65 vs 0.8). RRF is parameter-free at the application level.

The downside: RRF discards score magnitudes. A chunk ranked #1 by both retrievers by a HUGE margin gets the same score as one ranked #1 by a barely-passing margin. Doesn't matter for the use cases in scope (voice-matching, chat grounding, dedup). If Phase 6 measures degraded voice-matching, the fix is to stack a Haiku 4.5 reranker over the RRF top-15.

### Why defer Postgres backend

Phase 9 builds the VPS deploy. Until then, no environment uses Postgres, so building it would be untested. Cleanest pattern: define the SAME public API in `db.py` for both backends; the Postgres path raises `NotImplementedError`. Phase 9 implements the same 8 functions for `psycopg` + pgvector — focused, isolated.

### Why no Agent SDK calls in Phase 3

The PRD's Phase 3 contains zero references to `claude_agent_sdk`. Indexing and search are deterministic. This pays off in Phase 6: heartbeat can call `memory_search.py` synchronously from inside its own SDK call without recursion concerns. The recursion guard pattern is **only for scripts that themselves invoke the Agent SDK**.

### Asymmetric BGE embeddings

BGE was trained with a query/passage prefix asymmetry. Queries get `"Represent this sentence for searching relevant passages: "` prepended; passages get nothing (or a different prefix per variant). FastEmbed's `query_embed` and `passage_embed` apply the right prefix automatically. Use both in the right places. Calling `.embed()` on both sides degrades retrieval ~5–10% per the BGE paper.

### Tokenizer choice

We chunk on token boundaries using BGE's own tokenizer (HuggingFace `tokenizers` lib, no torch dep). This guarantees chunks fit BGE's 512-token context with margin (400 + 50 overlap leaves room for the asymmetric query prefix at retrieval time). Whitespace splitting would over- or under-fill the context window unpredictably across languages — Portuguese tokenizes ~1.3× more tokens-per-character than English in BGE's vocab.

### Excluding `personal/finance.md`

SOUL.md and CLAUDE.md both list "no financial data" as a hard boundary. Indexing `finance.md` would put its content into `chunks.content` (plain text, no encryption), where it could surface in any future RAG retrieval — including chat answers and draft generation. The exclusion is at the indexer level (`EXCLUDE_RELATIVE = {"personal/finance.md"}`); other `personal/*.md` files remain in scope.

### Vault is read-only in Phase 3

No script in this phase writes to `BrunOS/Memory/`. State lives at `.claude/data/state/memory.db` and the embedding cache at `.claude/data/fastembed_cache/`. This means Phase 3 doesn't interact with the eventual vault git-sync (Phase 9's `concat-both` driver, vault-internal `.gitignore`).

### Concurrency notes (deferred to later phases)

- **Phase 6** will run `memory_index.py` from heartbeat (every 30 min). It should hold a process lock (`shared.file_lock`) on a sentinel file to prevent overlap if a tick runs long.
- **Phase 7** chat bot will read concurrently while heartbeat writes. SQLite default journal mode is fine for single-writer + multi-reader. If contention shows, enable WAL: `conn.execute("PRAGMA journal_mode=WAL")`. Not enabled by default in Phase 3 — premature.
- **Phase 9** vault sync runs every 2 min; the DB is local-only (gitignored), so sync is irrelevant.

### Confidence Score

**8/10** that execution succeeds in one pass. The 2 points of risk:

1. **Phase 2 dep timing**: if `shared.vault_path()` lands with a different signature or behavior than this plan assumes, the verify step catches it but execution stalls. Mitigated by the verify-first task and (if needed) a 5-line local helper as escape hatch.
2. **sqlite-vec WHERE-on-joined-column behavior** with `--path-prefix`: 0.1.x has shifting behavior between point releases. The over-fetch + post-filter pattern is the safety net; if even that fails, fall back to in-Python filtering after retrieving inner_k=200 unfiltered.
3. **(0.5)** **BM25 sort direction**: FTS5 `bm25()` returns negative — easy to invert by mistake. Level 7 validation catches this (no `lisa.md` in top-5 = sort wrong).

The plan is denser on schema and gotchas than Phase 0 because the surface is more nuanced. Phase 0 was "create files"; Phase 3 is "create files that interact across two virtual table types in a pre-1.0 SQLite extension."

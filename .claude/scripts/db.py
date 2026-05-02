"""SQLite + sqlite-vec + FTS5 backend for Phase 3 hybrid memory search.

Public API mirrors what the Postgres+pgvector backend will expose in Phase 9;
the Postgres path raises NotImplementedError until then.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import sqlite_vec

DB_PATH = Path(__file__).parent.parent / "data" / "state" / "memory.db"

EMBED_DIM = 384

_SCHEMA = f"""
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
  embedding float[{EMBED_DIM}]
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
"""


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


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def upsert_chunk(
    conn: sqlite3.Connection,
    file_path: str,
    chunk_idx: int,
    content: str,
    mtime: float,
    embedding: np.ndarray,
) -> int:
    blob = embedding.astype(np.float32).tobytes()
    cur = conn.execute(
        "INSERT INTO chunks(file_path, chunk_idx, content, mtime) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(file_path, chunk_idx) DO UPDATE SET "
        "content=excluded.content, mtime=excluded.mtime "
        "RETURNING id",
        (file_path, chunk_idx, content, mtime),
    )
    chunk_id = cur.fetchone()["id"]
    conn.execute("DELETE FROM chunk_vec WHERE rowid = ?", (chunk_id,))
    conn.execute(
        "INSERT INTO chunk_vec(rowid, embedding) VALUES (?, ?)", (chunk_id, blob)
    )
    return chunk_id


def delete_chunks_for_file(conn: sqlite3.Connection, file_path: str) -> int:
    ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM chunks WHERE file_path = ?", (file_path,)
        )
    ]
    if not ids:
        return 0
    conn.executemany("DELETE FROM chunk_vec WHERE rowid = ?", [(i,) for i in ids])
    conn.execute("DELETE FROM chunks WHERE file_path = ?", (file_path,))
    return len(ids)


def vector_search(
    conn: sqlite3.Connection,
    qemb: np.ndarray,
    k: int,
    path_prefix: str | None = None,
) -> list[dict]:
    qblob = qemb.astype(np.float32).tobytes()
    if path_prefix is None:
        rows = conn.execute(
            "SELECT v.rowid AS id, v.distance, c.file_path, c.chunk_idx, c.content "
            "FROM chunk_vec v JOIN chunks c ON c.id = v.rowid "
            "WHERE v.embedding MATCH ? AND k = ? "
            "ORDER BY v.distance",
            (qblob, k),
        ).fetchall()
    else:
        inner_k = max(k * 5, 50)
        rows = conn.execute(
            "SELECT v.rowid AS id, v.distance, c.file_path, c.chunk_idx, c.content "
            "FROM chunk_vec v JOIN chunks c ON c.id = v.rowid "
            "WHERE v.embedding MATCH ? AND k = ? "
            "AND c.file_path LIKE ? || '/%' "
            "ORDER BY v.distance LIMIT ?",
            (qblob, inner_k, path_prefix, k),
        ).fetchall()
    return [dict(r) for r in rows]


def keyword_search(
    conn: sqlite3.Connection,
    query: str,
    k: int,
    path_prefix: str | None = None,
) -> list[dict]:
    sql = (
        "SELECT c.id, c.file_path, c.chunk_idx, c.content, bm25(chunk_fts) AS score "
        "FROM chunk_fts JOIN chunks c ON c.id = chunk_fts.rowid "
        "WHERE chunk_fts MATCH ? "
    )
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


def all_file_mtimes(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute(
        "SELECT file_path, MAX(mtime) AS mtime FROM chunks GROUP BY file_path"
    ).fetchall()
    return {r["file_path"]: r["mtime"] for r in rows}


def get_chunks(conn: sqlite3.Connection, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, file_path, chunk_idx, content, mtime FROM chunks "
        f"WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    return {r["id"]: dict(r) for r in rows}

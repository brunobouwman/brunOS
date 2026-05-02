"""Incremental indexer for BrunOS/Memory/.

Walks Memory/**/*.md, chunks via offset-preserving BGE tokenizer, batch-embeds,
upserts. Detects deletions (files removed since last walk). Excludes
personal/finance.md per the SOUL.md no-financial-data boundary.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tokenizers import Tokenizer

from db import (
    all_file_mtimes,
    connect,
    delete_chunks_for_file,
    init_schema,
    upsert_chunk,
)
from embeddings import embed_passages
from shared import vault_path

TOKENIZER_MODEL = "BAAI/bge-small-en-v1.5"
CHUNK_TOKENS = 400
OVERLAP_TOKENS = 50
STEP = CHUNK_TOKENS - OVERLAP_TOKENS

EXCLUDE_RELATIVE = {"personal/finance.md"}

_tok: Tokenizer | None = None


def _get_tokenizer() -> Tokenizer:
    global _tok
    if _tok is None:
        _tok = Tokenizer.from_pretrained(TOKENIZER_MODEL)
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


def index(
    full: bool = False,
    paths: list[str] | None = None,
    dry_run: bool = False,
) -> int:
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--paths", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    n = index(full=args.full, paths=args.paths, dry_run=args.dry_run)
    print(f"indexed {n} files", file=sys.stderr)


if __name__ == "__main__":
    main()

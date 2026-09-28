"""Helpers for the isolated, stopped-writer browser rollback drill.

Never invoke against operational data. check_browser_flow owns all directories
and processes; snapshots are copied to new paths, never over existing data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path


def file_manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def copy_snapshot(source: Path, destination: Path) -> None:
    """Caller has stopped all writers. Fail rather than merge/overwrite."""
    shutil.copytree(source, destination)
    if file_manifest(source) != file_manifest(destination):
        raise RuntimeError("Snapshot file verification failed")


def inspect_chroma(path: Path, *, mutate: bool = False) -> dict:
    # Each invocation runs in a fresh process so Chroma releases every handle
    # before the orchestrator copies its SQLite and HNSW files on Windows.
    import chromadb
    from chromadb.config import Settings

    collection = chromadb.PersistentClient(
        path=str(path), settings=Settings(anonymized_telemetry=False),
    ).get_collection("interview_questions")
    rows = collection.get(include=["documents", "metadatas", "embeddings"])
    ordered = sorted(zip(rows["ids"], rows["documents"], rows["metadatas"],
                         rows["embeddings"], strict=True))
    digest = hashlib.sha256(json.dumps(ordered, sort_keys=True).encode()).hexdigest()
    result = collection.query(query_embeddings=[rows["embeddings"][0]], n_results=5)
    assert len(result["ids"][0]) == 5
    before = collection.count()
    if mutate:
        collection.delete(ids=[rows["ids"][0]])
        assert collection.count() == before - 1
    return {"count": before, "sha256": digest, "query_results": 5,
            "after_count": collection.count()}


def chroma_probe(path: Path, *, mutate: bool = False) -> dict:
    command = [sys.executable, "-m", "scripts.check_rollback", str(path)]
    if mutate:
        command.append("--mutate")
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    return json.loads(result.stdout)


def mutate_business_data(data: Path) -> None:
    with closing(sqlite3.connect(data / "job_assistant.db")) as connection:
        connection.execute("UPDATE resume_records SET score = 1")
        connection.execute("UPDATE interview_sessions SET status = 'in_progress'")
        connection.commit()
        assert connection.execute("SELECT score FROM resume_records").fetchone()[0] == 1
    exports = list((data / "exports").glob("*.pdf"))
    assert exports
    for path in exports:
        path.unlink()
    assert not list((data / "exports").glob("*.pdf"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chroma", type=Path)
    parser.add_argument("--mutate", action="store_true")
    args = parser.parse_args()
    print(json.dumps(inspect_chroma(args.chroma, mutate=args.mutate)))


if __name__ == "__main__":
    main()

"""Validate and import the interview question bank into ChromaDB."""

from __future__ import annotations

import argparse
from pathlib import Path

from pbl_jobs_finder.modules.question_bank import (
    DEFAULT_QUESTION_BANK_PATH,
    load_question_bank,
)
from pbl_jobs_finder.vector_store import create_default_vector_store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_QUESTION_BANK_PATH,
        help="UTF-8 JSON question bank",
    )
    parser.add_argument(
        "--persist-directory",
        type=Path,
        default=None,
        help="Override the configured ChromaDB directory",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    questions = load_question_bank(args.source)
    store = create_default_vector_store(
        persist_directory=args.persist_directory
    )
    imported = store.add_question_bank(questions)
    print(f"Imported {imported} questions; collection total: {store.count}")


if __name__ == "__main__":
    main()

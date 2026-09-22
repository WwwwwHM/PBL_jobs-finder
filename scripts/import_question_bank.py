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
        help="UTF-8 JSON or Markdown question bank",
    )
    parser.add_argument(
        "--persist-directory",
        type=Path,
        default=None,
        help="Override the configured ChromaDB directory",
    )
    parser.add_argument("--position", default="AI大模型应用开发工程师", help="Markdown position")
    parser.add_argument("--category", default="AI应用开发", help="Markdown category")
    parser.add_argument(
        "--difficulty", choices=("basic", "medium", "advanced"), default="medium",
        help="Markdown difficulty",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate without embedding or writing")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    questions = load_question_bank(
        args.source, position=args.position, category=args.category, difficulty=args.difficulty,
    )
    answered = sum(bool(question.reference_answer) for question in questions)
    if args.dry_run:
        print(f"Validated {len(questions)} questions; with reference answers: {answered}")
        return
    store = create_default_vector_store(
        persist_directory=args.persist_directory
    )
    imported = store.add_question_bank(questions)
    print(
        f"Imported {imported} questions; with reference answers: {answered}; "
        f"collection total: {store.count}"
    )


if __name__ == "__main__":
    main()

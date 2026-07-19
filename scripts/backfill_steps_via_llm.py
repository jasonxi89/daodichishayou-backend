"""LLM 补写菜谱步骤（只填空行，落 steps_source='llm'）。

容器内执行:
    python scripts/backfill_steps_via_llm.py [--limit N] [--dry-run] [--sleep 0.5]
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.crawler.steps_backfill import backfill_steps_via_llm  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.5)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    db = SessionLocal()
    try:
        stats = backfill_steps_via_llm(
            db,
            limit=args.limit,
            dry_run=args.dry_run,
            sleep_seconds=args.sleep,
        )
    finally:
        db.close()

    print(f"done: {stats}")
    return 1 if stats["circuit_broken"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

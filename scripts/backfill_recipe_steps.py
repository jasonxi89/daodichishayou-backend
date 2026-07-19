"""下厨房真实补爬菜谱步骤（可覆盖 llm 补写，绝不动已有 scraped）。

风控极敏感（实测 10s 间隔第 2 个请求即 CAPTCHA），默认 30s 间隔 + CAPTCHA 即停。
容器内执行:
    python scripts/backfill_recipe_steps.py [--limit N] [--dry-run] [--sleep 30]
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.crawler.steps_backfill import backfill_steps_via_scrape  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=30.0)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    db = SessionLocal()
    try:
        stats = backfill_steps_via_scrape(
            db,
            limit=args.limit,
            dry_run=args.dry_run,
            sleep_seconds=args.sleep,
        )
    finally:
        db.close()

    print(f"done: {stats}")
    return 1 if stats["circuit_broken"] or stats["captcha"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

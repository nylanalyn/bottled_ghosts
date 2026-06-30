import argparse
import asyncio
from pathlib import Path

from cellar.runtime import run_bottle
from cellar.storage import load_bottle, open_database


async def async_main(database: Path, bottle_id: int | None, migrate_only: bool) -> None:
    db = await open_database(database)
    try:
        if not migrate_only:
            if bottle_id is None:
                raise SystemExit("--bottle is required unless --migrate-only is used")
            await run_bottle(db, await load_bottle(db, bottle_id))
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="bottled-ghosts")
    parser.add_argument("--database", type=Path, default=Path("spirits.db"))
    parser.add_argument("--bottle", type=int)
    parser.add_argument("--migrate-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(async_main(args.database, args.bottle, args.migrate_only))

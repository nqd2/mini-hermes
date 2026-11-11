"""Operator command for checksum-verified extracted-file migration."""

from __future__ import annotations

import argparse
import asyncio

from .files import AgentFileContentStore


async def _run(batch_size: int, limit: int | None, apply: bool) -> int:
    store = AgentFileContentStore.configured()
    count = await store.count_legacy_files()
    if not apply:
        print(f'{count} legacy extracted file(s) need object-store migration; database unchanged.')
        return 0
    migrated = await store.migrate_legacy_files(batch_size=batch_size, max_files=limit)
    remaining = await store.count_legacy_files()
    print(f'checksum-verified={migrated} remaining={remaining}')
    return 0 if remaining == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-size', type=int, default=50)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--apply', action='store_true', help='write verified object copies and remove legacy JSON text')
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    raise SystemExit(asyncio.run(_run(args.batch_size, args.limit, args.apply)))


if __name__ == '__main__':
    main()

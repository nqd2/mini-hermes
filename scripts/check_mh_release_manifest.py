"""Validate the MH release notes shipped with a build."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packages' / 'backend' / 'open_webui' / 'mini_hermes'))
from release_manifest import validate_release_build


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--production', action='store_true')
    args = parser.parse_args()
    version = os.environ.get('MH_RELEASE_VERSION', 'dev')
    if args.production and version == 'dev':
        parser.error('Set MH_RELEASE_VERSION to a checked-in release version for production')
    try:
        validate_release_build(version)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()

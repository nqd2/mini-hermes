"""Validate the WeAI release notes shipped with a build."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packages' / 'backend' / 'weai' / 'releases'))
from release_manifest import latest_release_version, validate_release_build


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--production', action='store_true')
    args = parser.parse_args()
    try:
        version = os.environ.get('WEAI_RELEASE_VERSION') or latest_release_version()
        if args.production and version == 'dev':
            parser.error('Set WEAI_RELEASE_VERSION to a checked-in release version for production')
        validate_release_build(version)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()

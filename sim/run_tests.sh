#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"
PYTHONPATH="$here/src" python3 -m unittest discover -s tests -p 'test_*.py' -v

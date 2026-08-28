#!/usr/bin/env sh
# Launch cullr. Passes through any extra flags.
cd "$(dirname "$0")" || exit 1
exec python3 -m cullr --open "$@"

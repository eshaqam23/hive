#!/usr/bin/env bash
# Start the HIVE Synapse bridge on 127.0.0.1:8790 (needs build-core.sh and a running rosbridge).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec node "$HERE/server.mjs"

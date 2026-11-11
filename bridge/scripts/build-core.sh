#!/usr/bin/env bash
# Build the ACTUAL @corebrum/core package from the pinned Corebrum Synapse commit,
# outside OneDrive, for the HIVE Synapse bridge. Run inside Ubuntu 24.04 (WSL).
#
#   bash bridge/scripts/build-core.sh            # unpatched (for the compatibility check)
#   HIVE_CORE_PATCH=1 bash bridge/scripts/build-core.sh   # with bridge/patches/*.patch
set -euo pipefail
SYNAPSE_REPO=${SYNAPSE_REPO:-https://github.com/Corebrum/corebrum-synapse.git}
SYNAPSE_COMMIT=${SYNAPSE_COMMIT:-6f7b7840e2b3345f66412a0fb800f294cb94667c}
HOME_DIR=${HIVE_BRIDGE_HOME:-$HOME/.hive-bridge}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$HOME_DIR/corebrum-synapse"
OUT="$HOME_DIR/core"

mkdir -p "$HOME_DIR"
if [ ! -d "$SRC/.git" ]; then git clone -q "$SYNAPSE_REPO" "$SRC"; fi
git -C "$SRC" fetch -q origin
git -C "$SRC" checkout -q --detach "$SYNAPSE_COMMIT"
git -C "$SRC" reset -q --hard "$SYNAPSE_COMMIT"
echo "Synapse at $(git -C "$SRC" rev-parse HEAD)"

rm -rf "$OUT" && mkdir -p "$OUT"
cp -r "$SRC/packages/core/src" "$SRC/packages/core/tsconfig.json" "$OUT/"
cp "$SRC/tsconfig.base.json" "$HOME_DIR/tsconfig.base.json"
sed -i 's#"extends": *"../../tsconfig.base.json"#"extends": "../tsconfig.base.json"#' "$OUT/tsconfig.json"

PATCHED=none
if [ "${HIVE_CORE_PATCH:-0}" = "1" ]; then
  for p in "$HERE"/patches/*.patch; do
    (cd "$OUT" && patch -p1 --forward < "$p")
    PATCHED="$(basename "$p")"
  done
fi

# Exact versions from the Synapse pnpm-lock.yaml importer for packages/core (optional
# rclnodejs/node-datachannel are not needed for the rosbridge transport).
cat > "$OUT/package.json" <<JSON
{
  "name": "@corebrum/core",
  "version": "0.8.16",
  "type": "module",
  "main": "./dist/index.js",
  "types": "./dist/index.d.ts",
  "exports": { ".": { "types": "./dist/index.d.ts", "import": "./dist/index.js", "default": "./dist/index.js" } },
  "hive": { "synapse_commit": "$SYNAPSE_COMMIT", "patch": "$PATCHED" },
  "dependencies": {
    "@eclipse-zenoh/zenoh-ts": "1.10.2",
    "@foxglove/cdr": "3.5.0",
    "@foxglove/rosmsg": "4.2.2",
    "@foxglove/rosmsg2-serialization": "3.1.0",
    "ws": "8.19.0",
    "zod": "3.25.76"
  },
  "devDependencies": { "@types/node": "20.19.35", "@types/ws": "8.18.1", "typescript": "5.9.3" }
}
JSON
(cd "$OUT" && npm install --no-audit --no-fund --silent && npx tsc -p tsconfig.json --noEmitOnError false || true)
test -f "$OUT/dist/transport/rosbridge/actions.js" || { echo "core build failed"; exit 1; }
echo "Built @corebrum/core ($SYNAPSE_COMMIT, patch=$PATCHED) at $OUT"

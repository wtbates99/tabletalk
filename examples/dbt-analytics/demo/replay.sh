#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Replay a recorded eval run with no model call. demo/inference holds real
# gemma4:31b-cloud artifacts; strict replay fails on any fingerprint miss, so a
# pass proves the agent, manifest, semantics, prompt, and warehouse identity are
# exactly what was recorded. Expects `dbt` (dbt-duckdb) and `tabletalk` on PATH.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
example="$(dirname "$here")"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

step() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
quiet() { "$@" >"$work/log" 2>&1 || { cat "$work/log"; exit 1; }; }

source "$here/_copy.sh"
copy_example "$example" "$work"
cd "$work/project"

step "Load the example data into DuckDB (keeps the committed dbt artifacts)"
quiet dbt deps --profiles-dir .
quiet dbt seed --profiles-dir . --target-path .build-target
quiet dbt build --profiles-dir . --target-path .build-target
echo "ok"

step "tabletalk eval run revenue --cache-policy replay"
tabletalk eval run revenue --cache-policy replay

step "tabletalk ask revenue … --cache-policy replay (prints the answer receipt)"
question="What was recognized revenue in July 2026?"
tabletalk ask revenue "$question" --cache-policy replay
run_id="$(tabletalk ask revenue "$question" --cache-policy replay --format json |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["run_id"])')"

step "tabletalk receipt replay $run_id"
tabletalk receipt replay "$run_id"

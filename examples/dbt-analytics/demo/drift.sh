#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# The TableTalk demo: a routine dbt PR silently changes what a data agent can see.
# Runs offline on DuckDB. No model and no network. Works on a scratch copy, never
# on the checked-in example. Expects `dbt` (dbt-duckdb) and `tabletalk` on PATH.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
example="$(dirname "$here")"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

step() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
quiet() { "$@" >"$work/log" 2>&1 || { cat "$work/log"; exit 1; }; }
build() {
  quiet dbt build --profiles-dir .
  quiet dbt docs generate --profiles-dir .
}

source "$here/_copy.sh"
copy_example "$example" "$work"
cd "$work/project"

step "Build the dbt project on DuckDB"
quiet dbt deps --profiles-dir .
quiet dbt seed --profiles-dir .  # The raw.orders source reads this seed.
build
echo "dbt build and docs generate: ok"

step "Lock what each agent is approved to see"
tabletalk lock

step "A teammate opens a dbt PR"
sed -n '1p' "$here/pr.patch" | sed 's/^# //'
patch -p1 --quiet <"$here/pr.patch"
build
echo "dbt build passes. Every dbt test is green."

step "tabletalk plan --check"
set +e
tabletalk plan --check
code=$?
set -e
printf '\nexit %s: CI blocks the merge until someone reviews the agent change.\n' "$code"
test "$code" -eq 5

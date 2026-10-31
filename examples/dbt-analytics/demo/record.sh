#!/usr/bin/env bash
# shellcheck source-path=SCRIPTDIR
# Re-record demo/inference with a live model after an intended agent change.
# Records from a clean copy, so ignored local dbt output cannot leak into the
# fingerprint, then replaces the committed recordings. Needs the model in
# tabletalk.yaml, plus `dbt` and `tabletalk` on PATH.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
example="$(dirname "$here")"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

source "$here/_copy.sh"
copy_example "$example" "$work"
cd "$work/project"
rm -rf demo/inference

dbt deps --profiles-dir . >/dev/null
dbt seed --profiles-dir . --target-path .build-target >/dev/null
dbt build --profiles-dir . --target-path .build-target >/dev/null
tabletalk eval run revenue --cache-policy write-only

rm -rf "$here/inference"
cp -R demo/inference "$here/inference"
echo "Recorded $(find "$here/inference/artifacts" -type f | wc -l | tr -d ' ') artifacts into demo/inference."

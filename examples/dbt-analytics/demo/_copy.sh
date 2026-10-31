# Sourced by the demo scripts. copy_example SRC DEST copies the project in SRC to
# DEST/project as git sees it: tracked and new files, never gitignored dbt output
# such as target/run_results.json, which would change what the agent sees.
copy_example() {
  local src="$1" dest="$2/project" file
  mkdir -p "$dest"
  while IFS= read -r -d '' file; do
    [ -e "$src/$file" ] || continue # Deleted in the working tree.
    mkdir -p "$dest/$(dirname "$file")"
    cp -p "$src/$file" "$dest/$file"
  done < <(git -C "$src" ls-files -z --cached --others --exclude-standard .)
}

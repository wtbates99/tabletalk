# Configuration and resources

`tabletalk.yaml` is intentionally small:

```yaml
dbt:
  project_dir: .
  manifest: target/manifest.json
  catalog: target/catalog.json
  run_results: target/run_results.json
  target: dev

llm:
  provider: ollama
  model: gemma4:31b-cloud
  base_url: http://localhost:11434/v1
```

Commands first look for `tabletalk.yaml` in the current directory, then for
`tabletalk/tabletalk.yaml`. This keeps TableTalk in its own repository folder without requiring a
`--project-folder` option on every command.

`project_dir`, `manifest`, and optional `profiles_dir` paths are relative to `tabletalk.yaml`. The dbt
target remains in `profiles.yml`; environment variables are resolved only at runtime. Supported
adapters are SQLite, DuckDB, and Snowflake, all opened or governed as read-only.

Agents support only `group:`, `tag:`, `model:`, `source:`, `path:`, and `package:` selectors plus exclusions.
`include_parents` and `include_children` are explicit booleans. Ephemeral and disabled models are not
queryable. Sources require an explicit `source:SOURCE.TABLE` selector; lineage expansion never grants
source access. Physical relation names must resolve to one dbt unique ID in the agent scope.

`instructions` documents business rules the manifest cannot express, while `reject_if_contains`
provides a small deterministic guardrail for known concepts that the selected dbt resources cannot
answer. Rejections happen before an LLM call and should be used sparingly for unambiguous missing-data
boundaries, not as a general intent router.

Optional `catalog.json` enriches physical types and statistics; optional `run_results.json` adds recent
dbt-test health. Neither can add queryable resources. The manifest fingerprint changes with any
manifest content change and is included in every run; the catalog has its own fingerprint. `tabletalk
init` detects both artifacts beside the manifest automatically.

The model prompt receives each selected resource's relation, description, physical catalog types,
column descriptions, tests, constraints, owner, access, materialization, package, explicit join
metadata, and upstream/downstream lineage. Lineage is provenance, never automatic join permission.

Sensitive models use `meta: {sensitive: true}`; sensitive columns use column-level
`meta: {sensitive: true}` or model `meta.sensitive_columns`. The agent must explicitly list allowed dbt
unique IDs or column names under `allow_sensitive`.

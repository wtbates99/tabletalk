# Configuration and resources

`tabletalk.yaml` is small and versioned:

```yaml
schema_version: tabletalk.project/v1
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
  temperature: 0

inference:
  cache_policy: normal
  persist_prompts: false
```

All editable TableTalk resources require a `schema_version`. Run
`tabletalk migrate` to add version identifiers to pre-0.6 editable YAML files.
Immutable inference, report, result, and baseline artifacts fail on an unknown
schema. TableTalk does not rewrite those records.

## dbt ownership

dbt remains authoritative for resources, columns, types, tests, constraints,
owners, access, tags, groups, packages, descriptions, and lineage. TableTalk
agent files use selectors. They do not copy dbt metadata.

Supported selectors are `group:`, `tag:`, `model:`, `source:`, `path:`, and
`package:`. Lineage expansion never grants access to a source. Ephemeral and
disabled models are not queryable.

## Agent policy

Agent files may contain these TableTalk-specific fields:

- `supported_questions` and `unsupported_questions`
- `instructions`
- `knowledge` references
- `allow_sensitive` approvals
- `reject_if_contains` for exact missing-data boundaries
- row and timeout limits.

Use `reject_if_contains` only for a known concept that the selected dbt scope
cannot answer. Do not use it as a general intent classifier.

## Semantic status

| Status | Meaning | Can support an answer |
|---|---|---:|
| `trusted` | The information comes from an approved source with provenance. | Yes |
| `confirmed` | A user explicitly accepted the meaning. | Yes |
| `inferred` | A model proposed the meaning. A user has not confirmed it. | No |
| `missing` | TableTalk lacks required meaning. | No |
| `conflicting` | Approved sources give incompatible meanings. | No |

## Secrets and evidence retention

TableTalk resolves credentials at runtime. It removes secret-like model fields
from fingerprints. It never writes credentials into agent resources.

Inference artifacts store a digest of model messages by default. Set
`persist_prompts: true` only when exact prompt replay is more important than raw
prompt retention risk.

An expected clarification or refusal is also an inference artifact. TableTalk
stores its typed outcome and usage. Strict replay reproduces that outcome without
a model request.

Trace and report files store bounded evidence rows. Claim checks and result
replay require these rows. Sensitive columns still require explicit agent
approval. TableTalk has no automatic deletion service. The project operator
must set file access and retention rules for `.tabletalk/`.

# User journeys

All journeys converge on `define → compile → plan → evaluate → apply → ask` and
end with an answer that separates interpretation, verification, evidence, SQL,
and returned data.

## Builders

- **SQLite beginner:** install TableTalk and Ollama, pull a tested free local
  model, run `init`, select a database file, discover schema, review the starter
  agent/eval, compile, evaluate, apply, and ask. No paid key is required.
- **DuckDB data scientist:** connect a local read-only DuckDB file, select curated
  relations, define metrics and dates, evaluate against a deterministic fixture,
  apply, then inspect SQL and evidence.
- **Snowflake analytics engineer:** configure a least-privilege read role through
  environment variables, mount dbt metadata, compile and inspect semantic changes,
  run required evals, promote a receipt-backed artifact, and serve it.
- **dbt analytics team:** build dbt, mount `manifest.json`, select models/sources,
  add TableTalk-only semantic overrides and evals, then re-evaluate on dbt changes.
- **Application developer:** invoke a named applied artifact through the Python or
  HTTP API, render structured answer fields, and treat typed failures as failures.
- **Platform team:** standardize profiles and policies, run environment-specific
  evals, approve artifact digests, deploy multiple named agents, and retain receipts.

## Reviewers and operators

- **CI pipeline:** compile twice, verify stable digests, plan against applied state,
  run deterministic fake-model/unit tests and execution evals, and permit apply only
  when required receipts match the candidate digest.
- **Business user:** ask a question, clarify material ambiguity, read the direct
  answer and verification status, and expand evidence, SQL, and data.
- **Security reviewer:** inspect relation/column scope, read-only validation,
  credential handling, model endpoint, policy decisions, and audit receipts.
- **Wrong-answer investigation:** open the answer receipt, reproduce the artifact,
  model configuration, interpretation, plan, SQL, data, and claims; correct
  semantics/evals; re-plan and re-apply rather than editing hidden prompts.

At every stage, unavailable AI, database errors, invalid SQL, or missing evidence
produce an explicit typed failure. No local parser, cached prose, alternate
provider, or model-memory answer takes over.

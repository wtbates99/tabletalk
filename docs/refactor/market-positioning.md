# Market positioning

TableTalk defines the **trusted data agents as code** category: a governed
development and operating lifecycle for conversational access to data.

## Landscape

Direct categories include text-to-SQL products, conversational BI, and data-agent
platforms. Indirect categories include BI semantic layers, notebooks, dbt,
infrastructure-as-code, LLM evaluation tools, and internal chat applications.
Specific vendor capabilities and market shares require external research and are
intentionally not asserted here.

Database connection, schema introspection, prompt templates, chat UI, SQL
generation, and generic model routing are commodity capabilities. Defensible value
comes from their integration with deterministic compilation, semantic plans,
execution-based evals, promotion gates, artifact-linked receipts, claim-level
evidence, and reproducibility.

## Buyers, blockers, and wedge

Buyer personas are heads of data, analytics engineering leaders, data-platform
leaders, application engineering leaders, and governance/security owners.
Adoption blockers include uncertain correctness, unclear ownership, weak semantic
metadata, data access risk, unpredictable model costs, migration effort, and the
absence of a reviewable deployment process.

The strongest initial wedge is an eval-gated agent over an existing dbt-governed
Snowflake environment, with SQLite and DuckDB providing a zero-cost adoption path.
Evals are required because syntactically valid SQL can be semantically wrong.
Provenance is required because reviewers need to trace claims to executed data.
Reproducibility is required to debug changes and approve releases. dbt integration
matters because business-ready relations, descriptions, tests, and lineage already
live there.

## Packaging assumptions

The open source surface can include local compilation, local Ollama support,
database adapters, local evals, and inspectable artifacts. Potential proprietary
features include organization control planes, approval workflows, managed state,
RBAC/SSO, policy management, audit retention, and fleet analytics. These packaging
choices and willingness to pay are assumptions requiring customer validation.

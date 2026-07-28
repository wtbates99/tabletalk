# Reliability model

A reliable answer uses the intended governed data, applies disclosed semantics,
executes validated read-only SQL within declared scope, and supports every material
claim with returned evidence. It is tied to immutable agent and runtime metadata.

## Failure model

Semantic failures include wrong resources, metrics, filters, joins, grain, units,
or business rules. SQL failures include invalid dialect, scope escape, writes, and
resource exhaustion. Join failures include undeclared paths, fan-out, and
many-to-many multiplication. Time failures include implicit timezone, incomplete
periods, and wrong inclusive boundaries. Execution failures include connectivity,
authorization, timeout, and truncation. Narrative failures include unsupported,
overstated, or model-memory claims.

Material ambiguity must yield a clarification request or an explicit configured
default. A repair is limited, disclosed, and revalidated; it is never substitute
SQL from local rules. Model, authentication, malformed-output, database,
validation, and evidence failures are returned as typed failures. Providers are
never substituted implicitly.

## Provenance and verification

Every answer records agent/artifact digest, model name and endpoint identity,
database identity (without secrets), exact interpretation and dates, plan, SQL,
validation decisions, sources/columns/joins, execution metadata, returned evidence,
claim mappings, repairs, and eval receipt.

Statuses are:

- `verified`: all material claims map to sufficient executed evidence;
- `partially_verified`: useful result, but named claims lack sufficient evidence;
- `insufficient_evidence`: no material answer can be supported;
- `clarification_required`: semantics are materially ambiguous;
- `failed`: model, validation, execution, or policy stage failed.

The UI must never use color or fluent prose to obscure status. It separates direct
answer, verification, interpretation, evidence, SQL, data, and technical details.
Required evals tied to the candidate digest must pass before apply.

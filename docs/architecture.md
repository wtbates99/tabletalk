# Architecture and safety boundaries

The live and eval paths use one runtime:

```text
dbt artifacts -> semantic analysis -> proposal -> exact approval
                                              |
                                              v
question -> resolved agent -> trusted meaning -> model inference
         -> SQL AST validation -> read-only connector -> evidence rows
         -> evidence-linked answer -> hard checks -> result artifact
         -> deterministic metrics -> diagnosis -> reports
```

## Boundaries

| Boundary | Module | Rule |
|---|---|---|
| dbt ingestion | `manifest.py` | `manifest.json` alone can add queryable resources. |
| semantic analysis | `semantic_analysis.py` | Analysis labels trusted, confirmed, inferred, missing, and conflicting meaning. |
| proposal | `proposal_service.py` | Generation is deterministic unless the user requests `--infer`. |
| approval | `proposals.py` | Approval binds exact content, manifest digest, base digest, actor, and dependencies. |
| inference | `runtime` and `inference.py` | Inference records are separate from metric computation. |
| SQL safety | `validation.py` | The parsed AST determines actual resources and columns. |
| execution | `connections.py` | The connector opens or governs a read-only target. |
| evaluation | `evals` | Eval cases call the same runtime as live questions. |
| metrics | `metrics.py` | Metrics use saved trial results and paired keys. |
| diagnosis | `diagnostics.py` | Deterministic checks map to stable failure codes and owners. |
| reports | `reports.py` | JSON, Markdown, and JUnit use one report object. |

## Safety rules

Validation rejects multiple statements, writes, commands, a denylist of
functions, table functions, unknown or ambiguous relations, unknown columns,
joins without a condition, and unapproved sensitive data. It lowers integer
limits above `max_rows`. The [security model](security-model.md) lists what it
does not catch.

Lineage proves dependency. It does not prove join correctness. Join evidence
must come from constraints, relationship tests, explicit metadata, or approved
eval behavior.

The warehouse identity's permissions are the security boundary. SQL validation
is a second, deterministic check in front of it.

## Reproducibility

An inference artifact has a content address and a complete configuration
fingerprint. The cache index points from one fingerprint to one immutable
artifact. The artifact stores a trace or one typed non-answer outcome. Strict
replay makes no model request.

The local executor uses bounded threads and preserves task order. Evaluation
semantics do not depend on the executor. A future distributed executor can
implement the same small interface. Apache Spark is not a core dependency.

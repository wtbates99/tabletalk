# Concepts

## Agent

An Agent is a versioned YAML resource declaring one trusted data interface:
connection, allowed relations, metrics, time semantics, approved
relationships, rules, read-only policy, and required EvalSuites.

## Compiled artifact

Compilation merges Agent source, database metadata, and optional dbt metadata
into canonical JSON. Its SHA-256 digest identifies the exact semantics and
scope. Compilation never invokes a model.

## Plan and applied state

A plan compares candidate and applied artifacts semantically. Applied state
only points to immutable artifact and eval-receipt digests. Updating multiple
Agents is atomic, and a failed or missing suite leaves state unchanged.

## Structured runtime

An applied Agent processes a question through typed stages:

```text
question → interpretation → semantic plan → scoped SQL → evidence
         → calculations → grounded claims → verification
```

Every SQL statement is parsed before execution. Relation, column, join,
read-only, row-limit, and timeout policies come from the applied artifact.

## Verification

The runtime uses explicit statuses: `verified`, `verified_with_warnings`,
`ambiguous`, and `insufficient_evidence`. A successful-looking model response
cannot override failed SQL validation, absent evidence, or unsupported numeric
claims.

## EvalSuite

EvalSuites are executable regression resources. They can require relations and
columns, forbid access, constrain joins and budgets, and compare scalar, table,
ordered, unordered, keyed, approximate, empty, or shape results. A passing
receipt is valid only for the exact suite and artifact digests.

# Runtime safety and architecture

Every live and eval question follows one pipeline:

```text
manifest → resolved agent scope → interpretation/SQL → AST validation
         → read-only connector → evidence rows → answer/claims → trace verification
```

`manifest.py` owns artifact normalization, selection, graph navigation, and fingerprints. `agents.py`
contains only selector resources and resolved in-memory scopes. `validation.py` parses SQL and derives
actual model/column use. `connections.py` resolves the selected dbt target and exposes only read-only
query execution. `runtime` constructs the answer and shared trace. `evals` calls that exact runtime and
adds deterministic comparisons. `traces.py` persists one schema for live and eval records.

Validation rejects multiple statements, writes/commands, forbidden external-read functions,
out-of-scope or ambiguous relations, unknown columns, unconditioned joins, excessive limits, and
unapproved sensitive resources. Connector permissions remain the final security boundary.

Lineage proves dependency, not semantic join correctness. TableTalk never labels a join verified merely
because two nodes share a lineage edge. Safe join evidence belongs in dbt constraints, relationship
tests, explicit `meta`, and passing eval coverage.

Post-execution verification requires successful execution and validates every claim’s row/column
evidence. Numeric claims must appear in cited result cells. Correctness additionally requires an exact
approved eval-question match and passing deterministic comparisons. Reported models and columns always
come from the validated AST. Trace records hold fingerprints and identities, not credentials or
deployment state.

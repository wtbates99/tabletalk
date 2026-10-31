# Commands

| Command | Result |
|---|---|
| `tabletalk init` | Mount a parsed dbt project and create small project directories. |
| `tabletalk migrate [PATH]` | Add current schema identifiers to editable YAML resources. |
| `tabletalk propose` | Analyze semantics and create at most three review-only proposals. |
| `tabletalk proposal show [ID]` | Show exact proposal bytes, evidence, and diff. |
| `tabletalk proposal edit ID FILE --from PATH` | Create a new immutable proposal from an edited file. |
| `tabletalk proposal reject [ID] --reason TEXT` | Record a proposal rejection. |
| `tabletalk approve [ID]` | Approve exact proposal bytes and run written evals. |
| `tabletalk agent create` | Create a review-only agent proposal from explicit selectors. |
| `tabletalk agent list` | List approved agents and resolved model counts. |
| `tabletalk agent show NAME` | Show one approved agent and its resolved dbt scope. |
| `tabletalk eval create NAME` | Execute reviewed SQL and create a review-only eval proposal. |
| `tabletalk eval run [NAME]` | Run eval trials and write JSON, Markdown, and JUnit reports. |
| `tabletalk baseline approve NAME --from REPORT` | Approve exact prior trial results as a baseline. |
| `tabletalk baseline show NAME` | Show an approved baseline. |
| `tabletalk ask NAME QUESTION` | Run the shared runtime and show answer provenance. |
| `tabletalk lock` | Record each agent's resolved dbt scope in `tabletalk.lock.yaml`. |
| `tabletalk plan` | Diff the current manifest against the lock and list eval cases at risk. |
| `tabletalk doctor` | Validate artifacts, target access, selectors, and eval coverage. |

`tabletalk propose` accepts `--sql-import`, `--bi-import`, `--history-import`,
`--answer GAP=VALUE`, and `--infer`.

`tabletalk eval run` accepts `--case`, `--trials`, `--workers`, `--baseline`, and
`--cache-policy`. Cache policies are `normal`, `read-only`, `write-only`,
`replay`, and `disabled`.

`replay` fails on a cache miss. It never calls a model.

Exit code 3 means that one or more eval hard checks failed. Exit code 4 means
that a resource or approval failed validation. Exit code 5 means that
`tabletalk plan --check` found drift from `tabletalk.lock.yaml`, or that no lock
exists. Exit code 1 means that an operational step failed.

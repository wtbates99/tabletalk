# Answer receipt specification, v1

A receipt is a portable record of how a data agent formed one answer. It names
the question, the answer, the SQL, the evidence cells behind each claim, the dbt
resources and descriptions the agent used, their tests, lineage, freshness, and
dependent dashboards, a trust level, and the identity needed to replay the run.

- Schema identifier: `tabletalk.receipt/v1`
- JSON Schema: [`receipt-v1.schema.json`](receipt-v1.schema.json) (Draft 2020-12)
- Example: [`examples/receipt-v1.verified.json`](examples/receipt-v1.verified.json),
  a real receipt from the example project

`tabletalk ask` prints a receipt and stores it as
`.tabletalk/receipts/<run_id>.json`. `tabletalk receipt show`, `receipt schema`,
and `receipt replay` read them. Any tool may produce or consume receipts. A
producer does not need TableTalk.

## Design rules

1. **Evidence, not assertion.** Each claim lists the result cells (`row`, `column`)
   that support it. A consumer can check a claim against `result.rows`.
2. **No invented confidence.** `trust` is a discrete level derived from
   deterministic checks. It is never a model's self-reported probability.
3. **State what is unknown.** A missing artifact yields `null`, not a guess. For
   example, a test `status` is `null` when no `run_results.json` was available.
4. **Reproducible identity.** `identity` and `replay` hold every input that
   determines inference, so a recorded run can be replayed with no model call.

## Trust levels

| Level | Meaning |
|---|---|
| `verified` | An approved eval case matched this exact question, and the eval passed. |
| `grounded` | Every hard check passed and every claim cites result cells. No approved eval vouches for the number. |
| `unverified` | A hard check failed, or a matching approved eval failed. |

`trust_reasons` explains the level in plain sentences. Hard checks include
read-only execution, resources and columns in scope, sensitive-data rules, row
limits, successful execution, and claim support. They are listed in `checks`.

## Fields

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | `"tabletalk.receipt/v1"` | The specification version. |
| `run_id` | string | `run_` plus 16 hex characters of a SHA-256 over the full run record. |
| `created_at` | ISO 8601 string | When the answer was produced. |
| `question`, `answer` | string | The question asked and the answer text. |
| `trust`, `trust_reasons` | enum, string[] | See [trust levels](#trust-levels). |
| `claims[]` | `{text, evidence[{row, column}]}` | Factual statements and their supporting cells. |
| `interpretation` | object | The agent's reading: `intent`, `metrics`, `dimensions`, `start_date`, `end_date`, `assumptions`. The executed SQL decides whether a boundary is inclusive. |
| `query` | `{dialect, generated, executed}` | The model's SQL, and the SQL executed after validation (for example, with a row limit). |
| `result` | `{row_count, rows[]}` | Bounded evidence rows. Values are JSON. Decimals are strings. |
| `resources[]` | object | Each dbt resource the query read (below). |
| `checks[]` | `{name, passed, detail}` | Every deterministic verification. |
| `identity` | object | `agent`, `agent_digest`, `model`, `warehouse`, `manifest_digest`, `catalog_digest`, `semantic_digest`, `prompt_version`, `runtime_version`. |
| `replay` | `{inference_fingerprint, recorded, command}` | Whether a recorded inference exists, and the command that replays it. |

Each `resources[]` entry contains:

| Field | Meaning |
|---|---|
| `unique_id`, `relation` | The dbt unique ID and the warehouse relation. |
| `description` | The model description the agent saw. |
| `columns[]` | Columns the query read: `name`, `description` (direct or inherited), `data_type`, `sensitive`. |
| `tests[]` | Every dbt test on the resource with its last `status`, or `null`. |
| `upstream[]` | All ancestors, nearest first. |
| `freshness[]` | `dbt source freshness` results for upstream sources, from `sources.json`. |
| `exposures[]` | dbt exposures, such as dashboards, that depend on the resource. |

All digests use the form `sha256:<64 hex>`.

## Compatibility

Within v1, a new version may add optional fields. It does not remove, rename, or
change the meaning of a field. Consumers should ignore unknown fields. A breaking
change gets a new identifier, `tabletalk.receipt/v2`.

## Privacy and retention

`result.rows` contains warehouse data. TableTalk applies the agent's
sensitive-column rules before execution. Receipts still inherit the sensitivity
of the data they summarize. `.tabletalk/` is gitignored. TableTalk does not
delete receipts automatically, so apply a retention policy.

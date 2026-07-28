# Product thesis

## Definition

TableTalk is a reliability-first platform for defining, evaluating, deploying,
and operating trusted data agents as code. It connects governed database
resources to a versioned agent definition and returns answers with visible SQL,
sources, assumptions, calculations, and evidence.

The category is **trusted data agents as code**.

## Customer and promise

The initial customer is a data team that already owns curated SQLite, DuckDB, or
Snowflake data and wants a controlled natural-language interface without
accepting opaque model behavior. Primary users are analytics engineers, data
platform engineers, application developers, and analysts. Business users and
security reviewers consume the governed output.

The developer promise is: define, compile, plan, evaluate, and apply data agents
reproducibly. The organizational promise is: let people talk to enterprise data
without blindly trusting AI-generated answers.

## Principles

The priority order is correctness, semantic correctness, evidence, reproducibility,
evaluation, governance, usability, speed, then breadth. Correct failure is better
than false confidence. Models are replaceable runtime dependencies; artifacts,
evals, policies, provenance, and lifecycle are the product.

TableTalk never fabricates a fallback answer or SQL statement. A configured model,
database, validation, or evidence failure remains an explicit failure.

## Value and commercial case

The wedge is pre-deployment regression testing plus post-answer evidence for teams
with governed data. Commercial value comes from reducing the review and incident
cost of data-agent changes, supporting controlled promotion, and producing an
auditable record tying an answer to an agent, model, database, SQL, and evidence.

Likely enterprise packaging includes shared state, approval policies, SSO/RBAC,
audit retention, deployment environments, private networking, and fleet-wide eval
reporting. These are product hypotheses, not validated market facts.

## Non-goals

TableTalk is not a generic agent framework, BI replacement, model marketplace,
prompt library, ETL tool, or autonomous write agent. Broad database/provider
counts, canned demos, and prose without evidence are not success.

## Success measures

- time from install to first verified SQLite answer;
- required-eval pass rate and regression detection rate;
- percentage of material claims linked to returned evidence;
- rate of blocked unsafe or out-of-scope SQL;
- artifact and plan determinism;
- answer reproducibility from retained receipts;
- time to diagnose a wrong answer;
- adoption and promotion of evaluated agents.

Organizations distrust current tools because model output is probabilistic,
business semantics are implicit, joins and dates are easy to misinterpret, polished
answers hide weak evidence, provider/database failures can be concealed, and
behavior is rarely tied to a reviewable artifact and regression suite.

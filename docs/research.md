# Research evidence

This review uses primary papers and standards. It includes work that changes a
TableTalk decision. It excludes leaderboard summaries, vendor claims without a
method, and work that only adds agent loops.

## Evidence table

| Work and date | Research question and method | Important result | Limitation | TableTalk implication |
|---|---|---|---|---|
| [Spider 2.0](https://arxiv.org/abs/2411.07763), November 2024 | Can models complete 632 enterprise SQL workflow tasks over large schemas and several dialects? | The reported agent solved 17.0% of Spider 2.0 tasks. The same paper reports much higher results on older benchmarks. | Many tasks include long workflows and operations outside TableTalk's read-only scope. | **Adopt now.** Use real dbt project context. Test complex analytical behavior. Do not claim that simple benchmark accuracy predicts production success. |
| [Dr.Spider](https://arxiv.org/abs/2301.08881), January 2023 | How stable are text-to-SQL systems under 17 database, question, and SQL perturbations? | The strongest tested system lost 14.0% overall and 50.7% on the hardest perturbation. | Perturbations diagnose robustness. They do not model company business meaning. | **Adopt now.** Generate paraphrase, boundary, and schema-change regressions. Preserve every trial. |
| [PRACTIQ](https://aclanthology.org/2025.naacl-long.13/), April 2025 | How do systems handle practical ambiguous and unanswerable questions in four-turn conversations? | Tested systems struggled with both classification and clarified SQL generation. | The conversations are generated from public datasets. They are not direct production logs. | **Adopt now.** Treat clarification and refusal as correctness behavior. Ask targeted questions. |
| [ABISS](https://arxiv.org/abs/2607.23340), July 2026 | Can interactive benchmarks cover eight ambiguity and unanswerable categories? | Eight open models often detected a problem but failed to identify or resolve its exact category. | This is a recent preprint. Its simulated users may not match real users. | **Prototype behind an experimental boundary.** Expand ambiguity categories only when deterministic evidence can generate them. Do not add a multi-agent simulator to the product. |
| [Semantic-layer-mediated NL2SQL](https://arxiv.org/abs/2606.31041), June 2026 | Does a curated semantic layer and deterministic compiler improve enterprise NL2SQL? | The paper reports 94.15% execution accuracy on 547 Spider2-snow tasks. | It uses one main model and one benchmark. It also warns about semantic-layer quality and overfitting. | **Adopt now.** Reuse curated dbt semantics. Treat incomplete semantics as a refusal boundary. **Defer** a new intermediate query language. |
| [Bootstrapping Semantic Layer from Execution](https://arxiv.org/abs/2606.05634), June 2026 | Can execution observations resolve missing phrase-to-data groundings? | Execution-backed grounding improved the tested baselines and created reusable memory entries. | Execution can support a hypothesis without proving business correctness. | **Prototype behind an experimental boundary.** Record execution observations as untrusted evidence. Never promote them without approval. |
| [Pervasive Annotation Errors Break Text-to-SQL Benchmarks](https://arxiv.org/abs/2601.08778), January 2026 | How often are BIRD and Spider 2.0-Snow labels wrong, and how do errors affect rankings? | Expert review found high error rates in sampled labels. Corrected labels changed system ranks. | The audit covers selected benchmark subsets. It does not measure every private eval suite. | **Adopt now.** Version evals, record provenance, run reference queries, and diagnose invalid ground truth. |
| [ELT-Bench-Verified](https://arxiv.org/abs/2603.29399), version 2, April 2026 | Do benchmark defects understate data-engineering agent capability? | An auditor-corrector process found ambiguous specifications, rigid checks, and incorrect ground truth. Human agreement was reported as Fleiss' kappa 0.85. | The task is ELT pipeline construction, not read-only analytics. Its audit also uses model assistance. | **Adopt now.** Require human review of generated evals. Keep deterministic evidence separate from model suggestions. |
| [Both Ends Count](https://arxiv.org/abs/2602.21480), version 4, April 2026 | Do accuracy-only metrics describe text-to-SQL behavior at data scale? | The paper reports large latency and cost differences that accuracy did not show. | It evaluates a selected model and query set. Large-data execution is outside the local example. | **Adopt now.** Record latency, tokens, and configured cost. Enforce budgets. **Defer** large-data performance claims. |
| [Spark-LLM-Eval](https://arxiv.org/abs/2603.28769), January 2026 preprint | Can distributed evaluation combine response caching, uncertainty, and paired tests? | The design separates inference from metrics. It uses content-addressed caching, confidence intervals, McNemar tests, and Wilcoxon tests. | The paper targets very large workloads. It is a single-author preprint. TableTalk does not need Spark for local suites. | **Adopt now.** Add immutable inference artifacts, strict replay, repeated trials, uncertainty, and paired tests. **Reject** Spark as a core dependency. |
| [Justice or Prejudice?](https://arxiv.org/abs/2410.02736), October 2024 | Which systematic biases affect model judges? | The study identifies and measures 12 judge biases across several models. | Its tasks are broader than data-agent evaluation. Bias measurements do not remove every judge failure. | **Reject as unnecessary complexity.** Do not use a model judge when SQL execution and evidence checks can decide correctness. Label optional model diagnosis as a suggestion. |
| [CHESS](https://arxiv.org/abs/2405.16755), May 2024 | Can targeted database context and execution-based revision improve text-to-SQL? | The system combines schema selection, relevant descriptions, candidate SQL, execution, and one revision stage. | It depends on value retrieval and vector infrastructure that can add retention and attack risks. | **Adopt now.** Supply selected dbt context and permit one validator-guided SQL repair. **Reject** a default vector database. |
| [ALCE](https://arxiv.org/abs/2305.14627), May 2023 | Can automatic metrics measure answers with cited supporting evidence? | The benchmark found incomplete citation support even in its strongest tested systems. | Citation support does not prove that the source or ground truth is correct. | **Adopt now.** Bind answer claims to exact evidence rows and columns. Record source digests for proposal fields. |
| [W3C PROV-O](https://www.w3.org/TR/prov-o/), Recommendation, April 2013 | How can systems represent and exchange provenance across contexts? | The standard separates entities, activities, agents, and their relationships. | Full RDF and OWL support would add more structure than this project needs. | **Adopt now.** Record sources, activities, actors, locators, and digests. **Reject** an RDF dependency for the current scope. |

## Repeated lessons

1. Domain context matters more than extra agent loops.
2. Execution equivalence is stronger than SQL string matching.
3. Ground truth still requires audit, provenance, and versioning.
4. Ambiguity and refusal are part of correctness.
5. A semantic layer helps only when its definitions are curated.
6. Production evaluation must include latency, cost, safety, and change.
7. Reproducibility requires the full inference and execution identity.
8. Human approval must prevent silent promotion of model inference.

## Product decisions

| Decision | Status | Reason |
|---|---|---|
| Proposal-first authoring with exact approval | Adopt now | Generated context is useful only when a user controls resource writes. |
| Trusted semantic additions with source digests | Adopt now | dbt can lack business definitions that a safe answer needs. |
| Explicit SQL, BI query, and history imports | Adopt now | Organizational queries can supply evidence without silent scraping. |
| Untrusted execution-grounded semantic memory | Prototype behind an experimental boundary | Execution narrows hypotheses but does not prove business meaning. |
| Multi-turn simulated-user benchmark generation | Defer | The first release needs strong static ambiguity and refusal cases. |
| Deterministic compiler for a new query language | Defer | dbt selectors, semantic statements, and SQL validation cover the current scope. |
| Model-based correctness judge | Reject as unnecessary complexity | Deterministic execution and evidence checks are stronger for this product. |
| Spark evaluation backend | Reject as unnecessary complexity | A bounded local executor covers current suite sizes. |
| Default vector database | Reject as unnecessary complexity | dbt metadata is already scoped and reviewable. |

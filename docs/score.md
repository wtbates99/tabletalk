# Agent-readiness score

`tabletalk score` rates how ready each agent's dbt scope is for a data agent, from
0 to 100. It is a fixed rubric of deterministic checks. Each point that a check
withholds comes with the fix that earns it back. The project score is the mean of
the agent scores.

```console
tabletalk score
tabletalk score --format json
tabletalk score --badge .github/badges/agent-ready.json
```

## Rubric

| Criterion | Points | Full points when |
|---|---:|---|
| Model descriptions | 10 | Every model in scope has a dbt description. Partial credit by share. |
| Column descriptions | 20 | Every column in scope has a description. Partial credit by share. |
| dbt tests | 15 | Every model in scope has at least one dbt test. Partial credit by share. |
| Sensitive data flagged | 10 | No column looks sensitive without `meta.sensitive`. Partial credit by share of models. |
| Semantics resolved | 10 | Each measure with several date columns has an approved `time_column`, and no approved statements conflict. |
| Scope locked | 15 | The agent's scope matches `tabletalk.lock.yaml`. |
| Eval coverage | 20 | 15 for at least one case with checked results; 5 for at least one rejection or ambiguity case. |

Grades: A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, otherwise F.

## What the score is not

The score measures metadata, review, and test coverage. It does not measure
answer accuracy. Use `tabletalk eval run` for that. A high score means an agent
has what it needs to be checked, not that it is right.

## README badge

Commit the file that `--badge` writes, then point shields.io at its raw URL:

```markdown
![agent-ready](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/OWNER/REPO/main/.github/badges/agent-ready.json)
```

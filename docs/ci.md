# Pull-request checks

The TableTalk action runs on a dbt pull request. It posts one comment that
shows how the change alters what each agent can see and whether recorded agent
behavior still holds.

```yaml
name: TableTalk
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  plan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      # Build manifest.json and catalog.json the way your project already does.
      - run: dbt deps && dbt parse && dbt docs generate
      - uses: wtbates99/tabletalk@main
        with:
          project-folder: .
          evals: replay
```

## What it checks

1. `tabletalk plan --check` compares the manifest with `tabletalk.lock.yaml`.
   Removed or renamed columns, new resources in an agent's scope, newly
   exposed sensitive columns, and changed descriptions all appear in the
   comment, together with the eval cases they put at risk.
2. With `evals: replay`, `tabletalk eval run --cache-policy replay` replays
   committed inference recordings. It makes no model request, so it needs no
   API key. Replay needs warehouse access for reference queries.

## Inputs and exit codes

| Input | Default | Meaning |
|---|---|---|
| `project-folder` | `.` | Folder that contains `tabletalk.yaml`. |
| `evals` | `none` | `replay` replays committed recordings. |
| `fail-on-drift` | `true` | Fail when any agent differs from the lock. |
| `comment` | `true` | Post or update one PR comment. |
| `python-version` | `3.12` | Python for TableTalk. |

The job always writes the same Markdown to the GitHub job summary. Pull
requests from forks receive a read-only token. The action then skips the
comment with a warning.

The job fails with exit code 3 when a replayed eval fails, 5 when an agent's
scope drifted, and 6 when replay has no recording for the change.

## Replay recordings

A recording's fingerprint covers what the model sees: the question, the agent
definition, the agent's in-scope dbt nodes and prompt context, semantics, model
configuration, prompt and runtime versions, and the warehouse identity. A dbt
change outside every agent's scope keeps replay valid. A change inside the scope
also appears in the plan, and replay then reports that no recording exists.

To accept the change, review the plan, then run these commands with a model
available:

```console
tabletalk lock
tabletalk eval run --cache-policy write-only
```

Commit `tabletalk.lock.yaml` and the inference store. Set `inference.store` in
`tabletalk.yaml` to a folder inside the repository. The example uses
`demo/inference`.

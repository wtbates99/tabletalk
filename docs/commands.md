# Commands

- `tabletalk init`: initialize from `dbt_project.yml`, a parsed manifest, and a dbt target.
- `tabletalk agent create`: guided selector preview and agent authoring.
- `tabletalk agent list`: list source agents and resolved model counts.
- `tabletalk agent show NAME`: show source, resolved node IDs, fingerprint, and warnings.
- `tabletalk eval create NAME`: execute and approve a question/reference case.
- `tabletalk eval run [NAME] [--case CASE] [--trials N]`: run deterministic hard-gate evals and
  optionally override the suite's independent-trial count.
- `tabletalk ask NAME QUESTION`: answer (quoted or unquoted), show provenance, and require passing exact eval coverage for a
  `VERIFIED` status.
- `tabletalk doctor`: fail on artifact, target, connectivity, selector, or eval-coverage blockers and
  report incomplete dbt descriptions as non-blocking metadata warnings.

`compile`, `plan`, `apply`, `connect`, `discover`, `connections`, and registry-style `agents` commands
were removed. A source agent is active immediately and asking never depends on applied state.

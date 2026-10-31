# Release plan for 0.6.0

Do not publish this release without explicit owner approval.

## Package identity

Version 0.6.0 uses this description:

> dbt-native development, evaluation, and observability for trusted data agents

The PyPI project page will take its description, README, license classifier,
links, and version from the built package. Publishing 0.6.0 will replace the
stale broad package description on the current release page. It will not change
the metadata of an older file.

After publication, set the GitHub About text to the same package description.
Keep the repository URL and topics aligned with the links and keywords in
`pyproject.toml`.

## Migration from releases before 0.6.0

1. Back up editable TableTalk resources.
2. Run `dbt parse` and `dbt docs generate`.
3. Run `tabletalk migrate` in the project root.
4. Run `tabletalk propose` to review the new small agent and eval format.
5. Approve exact proposal bytes.
6. Run each regression suite.
7. Approve a new baseline only after all failures are resolved or recorded.

`tabletalk migrate` adds schema identifiers to compatible project, agent, eval,
and semantic YAML. It rejects removed pre-dbt agent formats. Immutable old
artifacts are not rewritten.

Version 0.6.0 adds proposal records, approvals, provenance, semantic status,
inference artifacts, cache policies, replay, budgets, repeated trials, paired
baselines, failure diagnosis, and three report formats.

## Release checklist

- [ ] Run the full test suite.
- [ ] Run Ruff and mypy.
- [ ] Run the complete DuckDB example.
- [ ] Run the opt-in `gemma4:31b-cloud` Ollama smoke test.
- [ ] Build source and wheel distributions from a clean checkout.
- [ ] Inspect wheel and source contents.
- [ ] Confirm `LICENSE` and `LICENSE_HISTORY.md` agree.
- [ ] Confirm the wheel metadata uses version 0.6.0 and the current description.
- [ ] Install the wheel in a new virtual environment.
- [ ] Run `tabletalk --version` and `tabletalk --help` from that environment.
- [ ] Confirm docs match command help and example behavior.
- [ ] Update the GitHub About text after publication.
- [ ] Create release notes and migration notes.
- [ ] Ask the repository owner for publish approval.

Unresolved legal questions must go to an attorney. Engineering checks must not
reinterpret a license.

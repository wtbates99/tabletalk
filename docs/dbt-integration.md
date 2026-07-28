# dbt integration

TableTalk can enrich database metadata from a dbt manifest:

```yaml
dbt:
  project_dir: ../analytics
  target_dir: target
```

An explicit manifest path is also accepted. Compilation normalizes relation and
column descriptions, provenance, lineage, tests, materialization, tags, group,
and owner. The dbt artifact fingerprint is part of the candidate, so a manifest
change produces a new digest, plan, and eval requirement.

The database remains the execution authority. dbt metadata enriches semantics;
it does not grant access to relations outside the Agent's explicit scope.

`tabletalk connect --from-dbt PATH` can import supported connection structure.
Credentials should remain environment references. TableTalk does not maintain a
second global profile store.

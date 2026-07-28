# SQLite starter

This is the zero-infrastructure TableTalk walkthrough. It uses a read-only
SQLite connection and the free `gemma4:31b-cloud` development model through
your local Ollama daemon. The model is hosted by Ollama, so sign-in and network
access are required.

From this directory:

```bash
mkdir -p data
sqlite3 data/starter.db < evals/fixtures/schema.sql
sqlite3 data/starter.db < evals/fixtures/data.sql
ollama signin
ollama pull gemma4:31b-cloud
tabletalk compile sales
tabletalk plan sales
tabletalk apply sales
tabletalk ask sales "What was recognized revenue in January 2026?"
tabletalk serve
```

Default automated tests use deterministic fake models. A failed Ollama request
is reported and never replaced by handcrafted SQL, another provider, or a local
answer.

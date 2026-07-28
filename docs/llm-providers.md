# LLM Providers

tabletalk supports Ollama Cloud, OpenAI, and explicitly
configured OpenAI-compatible endpoints. New projects default to
`gemma4:31b-cloud` on Ollama's free tier. TableTalk never changes the configured
model/provider or generates a substitute answer after a failure.

---

## Ollama Cloud Free (default)

The Ollama daemon exposes the local endpoint while the configured `-cloud`
model runs through Ollama's hosted service. Sign-in and free-tier limits apply,
but no paid API key is required.

### Setup

1. Install Ollama: [ollama.com](https://ollama.com)
2. Sign in and pull the default:

```bash
ollama signin
ollama pull gemma4:31b-cloud
```

3. Confirm it's running:

```bash
ollama list
```

Ollama starts automatically and listens at `http://localhost:11434`.

### Configuration

```yaml
llm:
  provider: ollama
  api_key: ollama
  model: gemma4:31b-cloud
  base_url: http://localhost:11434/v1  # default Ollama OpenAI-compatible endpoint
  max_tokens: 2000
  temperature: 0
  reasoning_effort: none
  request_timeout_seconds: 60
```

### Development model

| Model | Pull command | Usage | Notes |
|-------|-------------|-------|-------|
| `gemma4:31b-cloud` | `ollama pull gemma4:31b-cloud` | Ollama Cloud free tier | **Development default** |

Model names and availability change. This table records the TableTalk version
and test date rather than promising permanent support. A model must follow
instructions, fit the schema context, produce reliable structured output, and
pass the agent's evals. Weaker models are not assumed equally reliable.

### Using a custom Ollama endpoint

If Ollama is running on a different host (e.g., a remote GPU server):

```yaml
llm:
  provider: ollama
  api_key: ollama
  model: gemma4:31b-cloud
  base_url: http://192.168.1.50:11434/v1
```

### Performance tips

- Temperature `0` is strongly recommended for SQL generation (deterministic output)
- `max_tokens: 2000` gives enough room for complex multi-join queries
- If Ollama or the configured model is unavailable, TableTalk surfaces the
  error and does not switch to a heuristic, cached answer, or another provider

## Free Ollama development

Development runs through the local Ollama daemon using
`gemma4:31b-cloud`. The model itself is hosted by Ollama, so sign-in, network
access, and free-tier availability are required. TableTalk never substitutes a
different model when it is unavailable.

Run the opt-in local smoke test with:

```bash
TABLETALK_LIVE_OLLAMA=1 uv run pytest tabletalk/tests/test_live_ollama.py
```

The default test suite skips this test and uses deterministic fake models, so CI
does not require network access, an Ollama daemon, or a downloaded model.

## OpenAI-compatible production

Use the primary compatibility contract for vLLM, LiteLLM, LM Studio,
OpenRouter, Azure-compatible gateways, or organization-managed proxies that
meet TableTalk's structured-generation requirements:

```yaml
llm:
  provider: openai-compatible
  base_url: ${TABLETALK_LLM_BASE_URL}
  api_key: ${TABLETALK_LLM_API_KEY}
  model: ${TABLETALK_LLM_MODEL}
  temperature: 0
  reasoning_effort: none
  request_timeout_seconds: 60
```

`base_url`, `api_key`, and `model` are mandatory. Compatibility is not implied
by a brand name: validate structured output, errors, context length, SQL
quality, and determinism with the live-model eval suite.

---

## OpenAI

```bash
export OPENAI_API_KEY=sk-...
```

### Configuration

```yaml
llm:
  provider: openai
  api_key: ${OPENAI_API_KEY}
  model: gpt-4o
  max_tokens: 1000
  temperature: 0
```

### Supported models

| Model | Notes |
|-------|-------|
| `gpt-4o` | **Recommended** — best SQL quality, fast, cost-effective |
| `gpt-4o-mini` | Faster and cheaper, good for simple schemas |
| `gpt-4-turbo` | Previous generation, still strong |
| `gpt-3.5-turbo` | Fast and cheap, lower quality on complex SQL |

### Configuration options

| Field | Default | Description |
|-------|---------|-------------|
| `model` | `gpt-4o` | Model name |
| `max_tokens` | `1000` | Max response tokens |
| `temperature` | `0` | Sampling temperature |
| `base_url` | OpenAI API | Custom endpoint (for Azure OpenAI or proxies) |

### Azure OpenAI

Point `base_url` at your Azure deployment:

```yaml
llm:
  provider: openai
  api_key: ${AZURE_OPENAI_KEY}
  model: gpt-4o                        # your deployment name
  base_url: https://myinstance.openai.azure.com/openai/deployments/gpt-4o
```

---

## Choosing a provider

| Scenario | Recommendation |
|----------|---------------|
| Getting started / demo | Ollama Cloud Free + `gemma4:31b-cloud` |
| Local development | Ollama Cloud Free + `gemma4:31b-cloud` |
| Privacy-sensitive data | An explicitly configured compatible private endpoint |
| Production accuracy | A compatible model that passes the agent's eval suite |
| High-volume production | `gpt-4o-mini` (cost/quality balance) |
| Complex multi-join schemas | A higher-capability compatible model validated by evals |

---

## Temperature and tokens

**Always use `temperature: 0` for SQL generation.** SQL is deterministic — you want the model to output the most likely correct query, not explore creative alternatives. Higher temperatures introduce random SQL errors.

**`max_tokens` guidelines:**

| Schema complexity | Recommended `max_tokens` |
|-------------------|--------------------------|
| Simple (1–3 tables, basic queries) | `500` |
| Moderate (3–10 tables, JOINs, CTEs) | `1000` |
| Complex (10+ tables, nested CTEs, window functions) | `2000` |

The LLM stops generating once it finishes the SQL — unused tokens are not charged by most providers.

---

## Environment variable security

Never hardcode API keys in `tabletalk.yaml`. Always use environment variable substitution:

```yaml
api_key: ${OPENAI_API_KEY}     # reads from environment at startup
```

tabletalk raises a clear error if the referenced variable is not set. For production deployments, inject secrets via your CI/CD platform or a secrets manager (AWS Secrets Manager, GCP Secret Manager, Vault).

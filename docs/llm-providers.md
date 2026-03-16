# LLM Providers

tabletalk supports three LLM backends: Ollama (local), OpenAI, and Anthropic. Configure the LLM in the `llm` block of `tabletalk.yaml`.

---

## Ollama (local, no API key)

Ollama runs models entirely on your machine. No API key, no data leaving your network, no per-token cost.

### Setup

1. Install Ollama: [ollama.com](https://ollama.com)
2. Pull a model:

```bash
ollama pull qwen2.5-coder:7b    # recommended default
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
  api_key: ollama                      # placeholder — Ollama doesn't validate keys
  model: qwen2.5-coder:7b
  base_url: http://localhost:11434/v1  # default Ollama OpenAI-compatible endpoint
  max_tokens: 2000
  temperature: 0
```

### Recommended models for SQL generation

| Model | Pull command | RAM required | Notes |
|-------|-------------|-------------|-------|
| `qwen2.5-coder:7b` | `ollama pull qwen2.5-coder:7b` | ~5 GB | **Default** — top SQL quality at 7B |
| `qwen2.5-coder:14b` | `ollama pull qwen2.5-coder:14b` | ~10 GB | More accurate on complex schemas |
| `qwen2.5-coder:32b` | `ollama pull qwen2.5-coder:32b` | ~20 GB | Best local SQL quality |
| `codellama` | `ollama pull codellama` | ~4 GB | Code specialist, strong SQL |
| `llama3.2` | `ollama pull llama3.2` | ~2 GB | Fast general-purpose |
| `mistral` | `ollama pull mistral` | ~4 GB | Strong structured output |
| `phi3` | `ollama pull phi3` | ~2 GB | Tiny and fast, basic SQL |

### Using a custom Ollama endpoint

If Ollama is running on a different host (e.g., a remote GPU server):

```yaml
llm:
  provider: ollama
  api_key: ollama
  model: qwen2.5-coder:7b
  base_url: http://192.168.1.50:11434/v1
```

### Performance tips

- Temperature `0` is strongly recommended for SQL generation (deterministic output)
- `max_tokens: 2000` gives enough room for complex multi-join queries
- For faster responses on small schemas, `qwen2.5-coder:7b` is near-instant on modern hardware

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

## Anthropic

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

### Configuration

```yaml
llm:
  provider: anthropic
  api_key: ${ANTHROPIC_API_KEY}
  model: claude-sonnet-4-6
  max_tokens: 1000
  temperature: 0
```

### Supported models

| Model | Notes |
|-------|-------|
| `claude-opus-4-6` | Highest capability, best for complex schemas |
| `claude-sonnet-4-6` | **Recommended** — strong SQL, fast, cost-effective |
| `claude-haiku-4-5-20251001` | Fast and cheap, good for simple queries |

---

## Choosing a provider

| Scenario | Recommendation |
|----------|---------------|
| Getting started / demo | Ollama + `qwen2.5-coder:7b` |
| Privacy-sensitive data | Ollama (data stays local) |
| Production accuracy | `gpt-4o` or `claude-sonnet-4-6` |
| High-volume production | `gpt-4o-mini` (cost/quality balance) |
| Complex multi-join schemas | `gpt-4o` or `claude-opus-4-6` |

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

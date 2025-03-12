# tabletalk

tabletalk is a command-line interface (CLI) tool designed to let you "talk" to your databases using natural language. Unlike heavier frameworks, tabletalk is built for simplicity and ease of use. With tabletalk, you can define specific "contexts" based on relationships in your data, then query that data conversationally, either by generating SQL or asking questions directly. It connects to your existing databases—BigQuery, SQLite, MySQL, or Postgres—pulls schemas based on your defined contexts, and leverages large language models (LLMs) from OpenAI and Anthropic to chat with your data effectively.

# Features

- Database Support: Connect to BigQuery, SQLite, MySQL, and Postgres.
- Custom Contexts: Define relationships in your data to create focused querying scenarios.
- LLM Integration: Use OpenAI or Anthropic models to generate SQL or answer questions.
- Natural Language Queries: Ask questions about your data in plain English, with SQL generated automatically.
- Local Execution: Run generated SQL locally against your database.

# Installation

Install tabletalk via pip:

```bash
pip install tabletalk
```

# Configuration

Tabletalk relies on a configuration file named tabletalk.yaml to set up your database and LLM preferences. This file includes:

- Provider: Details for connecting to your database.
- LLM: Settings for the language model, such as provider, API key, and model specifics.
- Contexts: Path to a directory containing context definitions.
- Output: Directory where manifest files (schema data) are stored.

Note: For security, set API keys as environment variables (e.g., export ANTHROPIC_API_KEY="your-key-here").

# Defining Contexts

Contexts are defined in separate YAML files within the contexts directory. Each context specifies a subset of your database—datasets and tables—relevant to a particular querying scenario.

- Provider: Details for connecting to your database.
- LLM: Settings for the language model, such as provider, API key, and model specifics.
- Contexts: Path to a directory containing context definitions.
- Output: Directory where manifest files (schema data) are stored.

Note: For security, set API keys as environment variables (e.g., export ANTHROPIC_API_KEY="your-key-here").

Here’s an example tabletalk.yaml:

```yaml
provider:
  type: mysql
  host: localhost
  user: root
  password: ${MYSQL_PASSWORD}
  database: test_store

llm:
  provider: anthropic
  api_key: ${ANTHROPIC_API_KEY}
  model: claude-3-5-sonnet-20240620
  max_tokens: 500
  temperature: 0

contexts: contexts
output: manifest
```

# Defining Contexts

Contexts are defined in separate YAML files within the contexts directory. Each context specifies a subset of your database—datasets and tables—relevant to a particular querying scenario.

Example context file contexts/sales_context.yaml:

```yaml
name: sales_context
datasets:
  - name: test_store
    tables:
      - customers
      - orders
```

# Usage

Tabletalk offers three core CLI commands:

```bash
tabletalk init
```

init: Sets up a new tabletalk project in your current directory, creating tabletalk.yaml, a contexts/ folder, and a manifest/ folder.

```bash
tabletalk apply
```

apply: Reads context definitions from the contexts directory, connects to your database, pulls the relevant schemas, and generates manifest files in the output directory (e.g., manifest/).

```bash
tabletalk query
```

query: Launches an interactive session where you select a manifest (representing a context) and ask questions in natural language. The LLM generates SQL queries based on your input.


# Example Workflow

Let’s set up and query a simple sales database:

Initialize the Project:

```bash
tabletalk init
```

This creates the project structure:

```text
project_folder/
├── tabletalk.yaml
├── contexts/
└── manifest/
```

Define a Context:

Create contexts/sales_context.yaml:

```yaml
name: sales_context
datasets:
  - name: test_store
    tables:
      - customers
      - orders
```

Configure tabletalk.yaml:

```yaml
name: sales_context
datasets:
  - name: test_store
    tables:
      - customers
      - orders
```

Apply the Schema:

```bash
tabletalk apply
```

This generates a manifest file (e.g., manifest/sales_context.json) with the schema for customers and orders.

Query Your Data:

```bash
tabletalk query
```

You’ll see a list of available manifests (e.g., 1. sales_context.json).
Enter the number (e.g., 1) to select it.
Ask a question like: "How many customers placed orders last month?"
The LLM generates an SQL query, which you can then run locally against your database.
Type exit to end the session.

# Contributing

Want to help improve tabletalk? Fork the repository, make your changes, and submit a pull request. For major updates, please open an issue first to discuss your ideas.

# License

This code is licensed under CC BY-NC 4.0 for non-commercial use. For commercial use, contact wtbates99@gmail.com.

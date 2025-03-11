=================
Context Files
=================

Overview
--------

Context files allow you to define different environments for your queries, such as development and production.
Each context specifies:

* **Provider**: The database/backend to pull table structures from
* **LLM**: The language model that generates SQL queries or helps with table structure generation
* **Datasets and Tables**: The data sources used in queries

Context files support including table head data as reference for the LLM, improving context generation.
This allows the LLM to better understand the data structure and provide more accurate responses.

Example Configuration
--------------------

Below is an example context file with two environments:

.. code-block:: yaml

    contexts:
      - name: dev_context
        provider:
          type: bigquery
          project_id: your-gcp-project-id
          use_default_credentials: true

        llm:
          provider: openai
          api_key: your-openai-api-key
          model: text-davinci-003
          max_tokens: 150
          temperature: 0

        datasets:
          - name: your-dataset-name
            tables:
              - your-table-name
              - your-other-table-name

      - name: prod_context
        provider:
          type: bigquery
          project_id: your-gcp-project-id
          use_default_credentials: false
          credentials: /path/to/service-account-key.json

        llm:
          provider: openai
          api_key: your-openai-api-key
          model: gpt-4
          max_tokens: 200
          temperature: 0.1

        datasets:
          - name: your-dataset-name
            tables:
              - your-table-name
              - your-other-table-name
          - name: your-other-dataset-name
            tables:
              - your-table-name
              - your-other-table-name

Configuration Options
--------------------

Provider
~~~~~~~~

* **type**: Database type (e.g., bigquery)
* **project_id**: Your project identifier
* **use_default_credentials**: Whether to use default authentication
* **credentials**: Path to service account key (when not using default credentials)

LLM
~~~

* **provider**: LLM provider (e.g., openai)
* **api_key**: Authentication key for the LLM service
* **model**: Specific model to use
* **max_tokens**: Maximum response length
* **temperature**: Controls randomness (0 = deterministic, higher = more random)

Datasets
~~~~~~~~

* **name**: Dataset identifier
* **tables**: List of tables within the dataset

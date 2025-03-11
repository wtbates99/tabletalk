====================
Supported Providers
====================

Origin supports two types of providers:

1. Data Providers - For connecting to databases and data sources
2. LLM Providers - For connecting to large language models

Providers are defined in the ``tabletext.yml`` configuration file, which is created during project initialization.

Data Providers
--------------

We support the following data providers:

BigQuery
~~~~~~~~

There are two ways to use the BigQuery provider:

1. Using a service account file
2. Using default credentials from Google Cloud SDK

Configuration Example
^^^^^^^^^^^^^^^^^^^^^

.. code-block:: yaml

    contexts:
      - name: dev_context
        provider:
          type: bigquery
          project_id: your-gcp-project-id
          # Choose one of the following authentication methods:

          # Option 1: Use service account key file
          credentials: /path/to/service-account-key.json

          # Option 2: Use default credentials
          use_default_credentials: true

This will create a BigQuery client instance with the project ID and credentials.

LLM Providers
-------------

We support the following LLM providers:

OpenAI
~~~~~~

Configuration Example
^^^^^^^^^^^^^^^^^^^^^

.. code-block:: yaml

    llm:
      provider: openai
      api_key: your-openai-api-key
      model: gpt-4
      max_tokens: 150
      temperature: 0

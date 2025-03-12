====================
Supported Providers
====================

Origin supports two types of providers:

1. **Data Providers** - For connecting to databases and data sources
2. **LLM Providers** - For connecting to large language models

Providers are defined in the ``tabletalk.yml`` configuration file, which is created during project initialization.

Data Providers
--------------

The following data providers are supported:

BigQuery
~~~~~~~~

There are two authentication methods for the BigQuery provider:

1. Using a service account file
2. Using default credentials from Google Cloud SDK

Configuration Example
^^^^^^^^^^^^^^^^^^^^^

.. code-block:: yaml

    provider:
      type: bigquery
      project_id: your-gcp-project-id
      # Choose one of the following authentication methods:

      # Option 1: Use service account key file
      credentials: /path/to/service-account-key.json

      # Option 2: Use default credentials
      use_default_credentials: true

This configuration creates a BigQuery client instance with the specified project ID and credentials.

Context Example
^^^^^^^^^^^^^^^

.. code-block:: yaml

    name: sales_context  # Name of the context
    datasets:
      - name: operations_dataset
        tables:
          - customers  # Table within the dataset
          - orders

SQLite
~~~~~~

Configuration Example
^^^^^^^^^^^^^^^^^^^^^

.. code-block:: yaml

    provider:
      type: sqlite
      database_path: /path/to/database.db

Context Example
^^^^^^^^^^^^^^^

.. code-block:: yaml

    name: sales_context  # Name of the context
    datasets:
      - name: sqlite
        tables:
          - customers  # Table within the dataset
          - orders

PostgreSQL
~~~~~~~~~~

Configuration Example
^^^^^^^^^^^^^^^^^^^^^

.. code-block:: yaml

    provider:
      type: postgres
      host: localhost
      database: test_store
      user: postgres
      password: userpassword

Context Example
^^^^^^^^^^^^^^^

.. code-block:: yaml

    name: sales_context  # Name of the context
    datasets:
      - name: public
        tables:
          - customers  # Table within the dataset
          - orders

MySQL
~~~~~

Configuration Example
^^^^^^^^^^^^^^^^^^^^^

.. code-block:: yaml

    provider:
      type: mysql
      host: localhost
      database: test_store
      user: root
      password: userpassword

Context Example
^^^^^^^^^^^^^^^

.. code-block:: yaml

    name: sales_context  # Name of the context
    datasets:
      - name: test_store
        tables:
          - customers  # Table within the dataset
          - orders

LLM Providers
-------------

The following LLM providers are supported:

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

Anthropic
~~~~~~~~~

Configuration Example
^^^^^^^^^^^^^^^^^^^^^

.. code-block:: yaml

    llm:
      provider: anthropic
      api_key: your-anthropic-api-key
      model: claude-3-sonnet-20240229

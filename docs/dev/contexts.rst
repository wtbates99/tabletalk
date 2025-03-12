=================
Context Files
=================

Overview
--------

Context files allow you to define different environments for your queries, such as development and production.
Each context specifies:

* **Datasets and Tables**: The data sources used in queries

Context files support including table head data as reference for the LLM, improving context generation.
This allows the LLM to better understand the data structure and provide more accurate responses.

Example Configuration
---------------------

Below is an example context file with two environments:

.. code-block:: yaml
  name: default_context
  description: "This context is a sample context."
  datasets:
    - name: test_store
      description: "Operational data for test store."
      tables:
        - name: customers
          description: "Customers table."
        - name: orders
          description: "Orders table."
        - purchase_orders
    - name: test_store_staging
      description: "Staging data for test store."
      tables:
        - margins

Configuration Options
---------------------

Datasets
~~~~~~~~

* **name**: Dataset identifier
* **tables**: List of tables within the dataset

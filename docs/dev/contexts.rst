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
--------------------

Below is an example context file with two environments:

.. code-block:: yaml

  name: example-context
  datasets:
    - name: example-dataset  # Name of the dataset
      tables:
        - example-table  # Table within the dataset
        - example-table-2  # Table within the dataset

Configuration Options
--------------------

Datasets
~~~~~~~~

* **name**: Dataset identifier
* **tables**: List of tables within the dataset

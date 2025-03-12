====================
tabletalk CLI Tool
====================

Overview
--------

The ``tabletalk`` CLI tool provides commands to manage and query database schemas using natural language. This document covers the usage of two key commands:

1. **``query``**: Starts an interactive session to query data using natural language.
2. **``serve``**: Launches a web server to provide a graphical interface for querying data.

Query Command
-------------

The ``query`` command starts an interactive session where users can ask natural language questions about their data. It generates SQL queries based on the selected manifest file, which contains the schema information.

Starting the Query Session
~~~~~~~~~~~~~~~~~~~~~~~~~~

To begin querying your data, use the following command:

.. code-block:: bash

    tabletalk query [PROJECT_FOLDER]

- **``PROJECT_FOLDER``**: Optional. The path to the project directory containing the ``tabletalk.yaml`` configuration file and the ``manifest`` folder. If not provided, it defaults to the current working directory.

If the project folder is invalid or the ``manifest`` folder does not exist, the tool will display an error message and prompt you to run the ``apply`` command first.

Using the Interactive Session
-----------------------------

Once the query session starts, follow these steps:

1. **Select a Manifest:**

   - The tool lists all available manifest files (e.g., ``sales.txt``, ``inventory.txt``) in the ``manifest`` folder.
   - Enter the number corresponding to the manifest you want to query.

2. **Ask Questions:**

   - After selecting a manifest, type a natural language question about the data (e.g., "What are the total sales by region?").
   - The tool generates and displays an SQL query based on your question and the selected manifest's schema.

3. **Change Manifests:**

   - To switch to a different manifest during the session, type ``change``.
   - You will be prompted to select a new manifest from the list.

4. **Exit the Session:**

   - To end the session, type ``exit``.

Examples
--------

**Example 1: Querying Sales Data**

.. code-block:: console

    $ tabletalk query
    Available manifest files:
    1. sales.txt
    2. inventory.txt
    Select a manifest file by number: 1

    Using manifest: sales.txt
    Type your question, 'change' to select a new manifest, or 'exit' to quit.
    > What are the total sales by region?

    Generated SQL:
    SELECT region, SUM(sales) FROM sales_data GROUP BY region;

**Example 2: Changing Manifests and Querying Inventory**

.. code-block:: console

    > change
    Available manifest files:
    1. sales.txt
    2. inventory.txt
    Select a manifest file by number: 2

    Switched to manifest: inventory.txt
    > Which items have stock below 5?

    Generated SQL:
    SELECT item_name FROM inventory WHERE stock < 5;

Prerequisites
-------------

.. note::

   Before using the ``query`` command, ensure the following:
   - The ``tabletalk.yaml`` file is properly configured in your project folder.
   - The ``apply`` command has been run to generate manifest files in the ``manifest`` folder. Run it with:

     .. code-block:: bash

         tabletalk apply [PROJECT_FOLDER]

   If the manifest folder is missing or empty, the ``query`` command will fail with an error message.

Error Handling
--------------

- **Invalid Project Folder**: If the specified ``PROJECT_FOLDER`` is not a valid directory, the tool will display:
  ``Error: '<folder>' is not a valid directory.``
- **Missing Manifests**: If the ``manifest`` folder doesn't exist or contains no ``.txt`` files, you'll see an appropriate error message.
- **Invalid Input**: When selecting a manifest, entering an invalid number prompts:
  ``Invalid selection. Please enter a valid number.``

---

Serve Command
-------------

The ``serve`` command launches a Flask web server that hosts an interactive web interface for querying database schemas using natural language. It provides a graphical alternative to the CLI-based ``query`` command.

Starting the Web Server
-----------------------

To launch the web interface, use the following command:

.. code-block:: bash

    tabletalk serve [--port PORT]

- **``--port PORT``**: Optional. Specifies the port on which the server will run. Defaults to ``5000``.

The server will start, and the interface will be accessible at ``http://localhost:PORT``.

Using the Web Interface
-----------------------

1. **Select a Manifest:**

   - The sidebar lists all available manifest files from the ``manifest`` folder.
   - Click on a manifest to select it. The summary will update to show the selected manifest's data source and context.

2. **Ask Questions:**

   - Type a natural language question in the chat input (e.g., "What are the top 5 customers by sales?").
   - Click "Send" to generate and display the SQL query in the chat history.

3. **Interact with the Chat:**

   - The chat history displays your questions and the generated SQL responses.
   - Use the input field to ask new questions or select a different manifest.

Examples
--------

**Example: Querying Data via the Web Interface**

1. Start the server:

   .. code-block:: bash

       tabletalk serve --port 8080

2. Open ``http://localhost:8080`` in a browser.
3. Select ``sales.txt`` from the sidebar.
4. Ask: "What are the total sales by region?"
5. Response in chat:
   - **You:** What are the total sales by region?
   - **tabletalk:** ``SELECT region, SUM(sales) FROM sales_data GROUP BY region;``

Prerequisites
-------------

.. note::

   Before using the ``serve`` command, ensure the following:
   - The ``tabletalk.yaml`` file is properly configured in your project folder.
   - The ``apply`` command has been run to generate manifest files in the ``manifest`` folder. Run it with:

     .. code-block:: bash

         tabletalk apply [PROJECT_FOLDER]

   If the manifest folder is missing or empty, the web interface will not display any manifests.

Error Handling
--------------

- **No Manifest Folder:** The server returns a 404 error if the ``manifest`` folder is missing.
- **No Manifest Selected:** Attempting to ask a question without selecting a manifest returns a 400 error.
- **Invalid Requests:** Missing or invalid data in requests (e.g., no question provided) results in appropriate error messages.

---

Summary
-------

- **``query`` Command:** Provides a CLI-based interactive session for querying data using natural language.
- **``serve`` Command:** Launches a web server with a graphical interface for querying data, offering a more visual and interactive experience.

Both commands require a properly configured project folder with generated manifest files. Ensure you run ``tabletalk apply`` before using either command.

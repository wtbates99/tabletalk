# Local Development

Local Development
================

Setting Up Your Development Environment
--------------------------------------

1. Clone the repository:

   .. code-block:: bash

      git clone https://github.com/yourusername/tabletalk.git
      cd tabletalk

2. Create and activate a virtual environment:

   .. code-block:: bash

      # Using venv
      python -m venv venv
      source venv/bin/activate  # On Windows: venv\Scripts\activate

      # Or using conda
      conda create -n tabletalk python=3.9
      conda activate tabletalk

3. Install development dependencies:

   .. code-block:: bash

      pip install -r requirements-dev.txt

4. Initialize pre-commit hooks:

   .. code-block:: bash

      pre-commit install

Development Workflow
-------------------

- Run the CLI directly during development:

  .. code-block:: bash

     python -m tabletalk run
     # or
     python -m tabletalk test

- Run tests:

  .. code-block:: bash

     pytest

Building and Installing Locally
------------------------------

1. Install build tools:

   .. code-block:: bash

      pip install build twine

2. Build the package:

   .. code-block:: bash

      python -m build

3. Install locally:

   .. code-block:: bash

      pip install dist/tabletalk-0.1.0-py3-none-any.whl

Distribution
-----------

To distribute on PyPI:

1. Build the package:

   .. code-block:: bash

      python -m build

2. Upload to PyPI (after registering on PyPI):

   .. code-block:: bash

      twine upload dist/*

Usage After Installation
----------------------

Once installed, users can:

- Run the CLI with:

  .. code-block:: bash

     tabletalk run
     tabletalk test

# Result
With this ``setup.py``, users can:
- Install your package with ``pip install tabletalk``.
- Run it as ``tabletalk run`` or ``tabletalk test`` from the command line, just like dbt.

This setup mirrors dbt's approach, adapted to your simpler application and specific requirements, ensuring a seamless user experience without compilation. Adjust the ``author``, ``author_email``, ``url``, and ``version`` as needed for your project.

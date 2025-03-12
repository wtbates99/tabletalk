#!/usr/bin/env python
import os
import sys

if sys.version_info < (3, 9):
    print("Error: tabletalk does not support this version of Python.")
    print("Please upgrade to Python 3.9 or higher.")
    sys.exit(1)

from setuptools import find_packages, setup  # type: ignore[import-untyped]

# Read the README.md for the long description
this_directory = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(this_directory, "README.md")) as f:
    long_description = f.read()

# Package metadata
package_name = "tabletalk"
package_version = "0.1.3"
description = "A command-line tool for managing database schemas and generating SQL queries using natural language."

# Setup configuration
setup(
    name=package_name,
    version=package_version,
    description=description,
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="william bates",
    author_email="wtbates99@gmail.com",
    url="https://github.com/wtbates99/tabletalk",
    packages=find_packages(),
    include_package_data=True,
    entry_points={
        "console_scripts": ["tabletalk = tabletalk.cli:cli"],
    },
    install_requires=[
        "pyyaml>=6.0",
        "openai>=1.0.0",
        "google-cloud-bigquery>=3.0.0",
        "sphinx",
        "mysql-connector-python",
        "psycopg2-binary",
        "anthropic",
        "click",
    ],
    extras_require={
        "dev": [
            "mypy",
            "types-setuptools",
        ],
    },
    python_requires=">=3.9",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "License :: CC BY-NC-SA 4.0",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)

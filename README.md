# Local Development
1. Clone the repo
2. Run `pre-commit install` to install pre-commit hooks and required Go tools
3. Run `go build` to build the project

# BigQuery Schema Analysis

This project analyzes BigQuery schemas and organizes them into modules for easier analysis.

## Getting Started

1. Edit the config.yaml file with your GCP project ID
2. Run the scan command to fetch schemas from BigQuery
3. Create modules in the modules/ directory to group related tables

## Commands

- init: Initialize the project structure
- scan: Scan BigQuery for table schemas
- analyze: Analyze tables based on defined modules

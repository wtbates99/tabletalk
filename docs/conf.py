# Configuration file for the Sphinx documentation builder.
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -------------------------------------------------------

project = "tabletalk"
copyright = "2025, william bates"
author = "william bates"
release = "0.5.0"

# -- General configuration -----------------------------------------------------

extensions = [
    "myst_parser",
]

# MyST parser settings — enable common markdown extensions
myst_enable_extensions = [
    "colon_fence",  # ::: fenced directives
    "deflist",  # definition lists
    "tasklist",  # - [ ] checkboxes
]

# Auto-generate anchors for headings up to level 3 (enables #anchor-name links)
myst_heading_anchors = 3

# Use index.md as the root document
root_doc = "index"

source_suffix = {
    ".md": "markdown",
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# -- Options for HTML output ---------------------------------------------------

html_theme = "alabaster"
html_static_path = []

html_theme_options = {
    "description": "dbt-native evaluation and observability for natural-language agents",
    "github_user": "wtbates99",
    "github_repo": "tabletalk",
    "github_button": True,
    "fixed_sidebar": True,
}

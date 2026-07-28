"""
tools.py — Tool call registry for agent function calling (item 14).

Agents can be given a set of named tools (Python callables) that the LLM
can invoke.  This module provides a lightweight registry, a JSON-schema
descriptor builder, and a dispatcher.

Usage:
    from tabletalk.tools import ToolRegistry

    reg = ToolRegistry()

    @reg.tool(description="Return today's date in ISO-8601 format")
    def get_today() -> str:
        from datetime import date
        return date.today().isoformat()

    # Describe all tools for the LLM (OpenAI function-calling format)
    schemas = reg.schemas()

    # Dispatch a call coming back from the LLM
    result = reg.call("get_today", {})
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("tabletalk")


class ToolRegistry:
    """Registry of callable tools that can be described to and invoked by an LLM."""

    def __init__(self) -> None:
        self._tools: Dict[str, Dict[str, Any]] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(
        self,
        func: Callable[..., Any],
        name: Optional[str] = None,
        description: str = "",
    ) -> None:
        """Register a callable as a named tool."""
        tool_name = name or func.__name__
        if not description:
            description = inspect.getdoc(func) or ""
        self._tools[tool_name] = {
            "name": tool_name,
            "description": description,
            "callable": func,
            "parameters": _infer_parameters(func),
        }
        logger.debug(f"Tool registered: {tool_name}")

    def tool(
        self,
        name: Optional[str] = None,
        description: str = "",
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form of register()."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register(func, name=name, description=description)
            return func

        return decorator

    def unregister(self, name: str) -> bool:
        """Remove a tool. Returns True if it existed."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def call(self, name: str, arguments: Dict[str, Any]) -> Any:
        """
        Invoke a registered tool by name with the given arguments dict.
        Raises KeyError if the tool is unknown, TypeError on bad arguments.
        """
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name!r}. Available: {list(self._tools)}")
        func = self._tools[name]["callable"]
        logger.debug(f"Tool call: {name}({arguments})")
        return func(**arguments)

    # ── Schema generation ─────────────────────────────────────────────────────

    def schemas(self) -> List[Dict[str, Any]]:
        """
        Return all tool schemas in OpenAI function-calling format:
        [{"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}]
        """
        result = []
        for entry in self._tools.values():
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": entry["name"],
                        "description": entry["description"],
                        "parameters": entry["parameters"],
                    },
                }
            )
        return result

    def list_tools(self) -> List[str]:
        return sorted(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# ── Parameter inference ───────────────────────────────────────────────────────

_PY_TO_JSON: Dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}


def _infer_parameters(func: Callable[..., Any]) -> Dict[str, Any]:
    """
    Build a minimal JSON Schema ``parameters`` object from the function signature.
    Supports str / int / float / bool / list / dict type hints only.
    Parameters with defaults are treated as optional; required = those without.
    """
    sig = inspect.signature(func)
    properties: Dict[str, Any] = {}
    required: List[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        json_type = "string"  # default
        ann = param.annotation
        if ann is not inspect.Parameter.empty:
            type_name = getattr(ann, "__name__", str(ann))
            json_type = _PY_TO_JSON.get(type_name, "string")

        properties[param_name] = {"type": json_type}

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


# ── Built-in utility tools ────────────────────────────────────────────────────

_builtin_registry = ToolRegistry()


@_builtin_registry.tool(description="Return today's date in ISO-8601 format (YYYY-MM-DD).")
def get_today() -> str:
    from datetime import date

    return date.today().isoformat()


@_builtin_registry.tool(description="Return the current UTC datetime as an ISO-8601 string.")
def get_utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def builtin_tools() -> ToolRegistry:
    """Return the registry of built-in utility tools."""
    return _builtin_registry

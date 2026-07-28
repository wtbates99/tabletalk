"""Tests for tabletalk/tools.py"""
from __future__ import annotations

import pytest

from tabletalk.tools import ToolRegistry, _infer_parameters, builtin_tools


class TestToolRegistry:
    def test_register_and_call(self):
        reg = ToolRegistry()
        reg.register(lambda: "hello", name="greet", description="Say hello")
        assert reg.call("greet", {}) == "hello"

    def test_register_with_decorator(self):
        reg = ToolRegistry()

        @reg.tool(description="Add two numbers")
        def add(a: int, b: int) -> int:
            return a + b

        assert reg.call("add", {"a": 2, "b": 3}) == 5

    def test_call_unknown_raises_keyerror(self):
        reg = ToolRegistry()
        with pytest.raises(KeyError, match="Unknown tool"):
            reg.call("nonexistent", {})

    def test_unregister(self):
        reg = ToolRegistry()
        reg.register(lambda: None, name="temp")
        assert reg.unregister("temp") is True
        assert reg.unregister("temp") is False

    def test_contains(self):
        reg = ToolRegistry()
        reg.register(lambda: None, name="foo")
        assert "foo" in reg
        assert "bar" not in reg

    def test_len(self):
        reg = ToolRegistry()
        assert len(reg) == 0
        reg.register(lambda: None, name="a")
        reg.register(lambda: None, name="b")
        assert len(reg) == 2

    def test_list_tools_sorted(self):
        reg = ToolRegistry()
        reg.register(lambda: None, name="z_tool")
        reg.register(lambda: None, name="a_tool")
        assert reg.list_tools() == ["a_tool", "z_tool"]

    def test_description_from_docstring(self):
        reg = ToolRegistry()

        def documented():
            """This is the docstring."""
            pass

        reg.register(documented)
        schemas = reg.schemas()
        assert schemas[0]["function"]["description"] == "This is the docstring."

    def test_explicit_description_overrides_docstring(self):
        reg = ToolRegistry()

        def documented():
            """Docstring."""
            pass

        reg.register(documented, description="Explicit")
        schemas = reg.schemas()
        assert schemas[0]["function"]["description"] == "Explicit"


class TestSchemas:
    def test_schema_format(self):
        reg = ToolRegistry()

        @reg.tool(description="Test tool")
        def my_func(x: str, y: int = 0) -> str:
            return x

        schemas = reg.schemas()
        assert len(schemas) == 1
        s = schemas[0]
        assert s["type"] == "function"
        assert s["function"]["name"] == "my_func"
        assert "parameters" in s["function"]
        params = s["function"]["parameters"]
        assert "x" in params["properties"]
        assert "y" in params["properties"]
        assert "x" in params["required"]
        assert "y" not in params["required"]  # has default


class TestInferParameters:
    def test_no_params(self):
        def f():
            pass

        schema = _infer_parameters(f)
        assert schema["properties"] == {}
        assert schema["required"] == []

    def test_required_param(self):
        def f(name: str):
            pass

        schema = _infer_parameters(f)
        assert "name" in schema["required"]
        assert schema["properties"]["name"]["type"] == "string"

    def test_optional_param(self):
        def f(limit: int = 10):
            pass

        schema = _infer_parameters(f)
        assert "limit" not in schema["required"]
        assert schema["properties"]["limit"]["type"] == "integer"

    def test_bool_type(self):
        def f(flag: bool):
            pass

        schema = _infer_parameters(f)
        assert schema["properties"]["flag"]["type"] == "boolean"

    def test_no_type_annotation_defaults_string(self):
        def f(x):
            pass

        schema = _infer_parameters(f)
        assert schema["properties"]["x"]["type"] == "string"


class TestBuiltinTools:
    def test_builtin_tools_not_empty(self):
        bt = builtin_tools()
        assert len(bt) > 0

    def test_get_today_returns_date_string(self):
        bt = builtin_tools()
        result = bt.call("get_today", {})
        assert len(result) == 10  # YYYY-MM-DD
        assert result[4] == "-"

    def test_get_utc_now_returns_datetime_string(self):
        bt = builtin_tools()
        result = bt.call("get_utc_now", {})
        assert "T" in result
        assert "+" in result or "Z" in result or result.endswith("+00:00")

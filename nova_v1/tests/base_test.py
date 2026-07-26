"""
Scratch test for tools/base.py, exercised against tools/example/demo_tool.py.

Run with: python -m pytest test_base.py -v
(from inside nova_test/, with nova_test/ on PYTHONPATH so `app.*` resolves)

This is a throwaway copy for verification only — the real test belongs at
nova_v1/tests/ once you're ready to commit one, importing from the actual
backend package rather than this scratch copy.
"""

import pytest

from app.tools.base import BaseTool
from app.tools.example.demo_tool import DemoTool


def test_demo_tool_exposes_contract_fields():
    tool = DemoTool()
    assert tool.name == "demo_echo"
    assert "Echoes" in tool.description
    assert tool.input_schema["type"] == "object"
    assert tool.input_schema["required"] == ["message"]


def test_invoke_delegates_to_execute():
    tool = DemoTool()
    result = tool.invoke({"message": "hello"})
    assert result == {"echoed": {"message": "hello"}}


def test_invoke_and_execute_agree_for_arbitrary_input():
    tool = DemoTool()
    payload = {"message": "anything", "extra": 123}
    assert tool.invoke(payload) == tool._execute(payload)


def test_base_tool_cannot_be_instantiated_directly():
    # BaseTool is abstract (_execute has no implementation) — this is what
    # forces every Function tool to actually implement _execute.
    with pytest.raises(TypeError):
        BaseTool(name="x", description="y", input_schema={})


def test_subclass_missing_execute_cannot_be_instantiated():
    class Incomplete(BaseTool):
        pass

    with pytest.raises(TypeError):
        Incomplete(name="x", description="y", input_schema={})


def test_base_tool_has_no_gain_or_schema_surface():
    # Regression guard for the option-2 split: BaseTool must never carry a
    # gain or be able to build a ToolSchema itself. If this starts failing,
    # someone re-added gain to the base class instead of the registry.
    tool = DemoTool()
    assert not hasattr(tool, "gain")
    assert not hasattr(tool, "to_schema")


def test_two_instances_are_independent():
    # No shared mutable state leaking between tool instances.
    a = DemoTool()
    b = DemoTool()
    assert a.invoke({"message": "one"}) != b.invoke({"message": "two"})
    assert a.name == b.name  # same contract, different instances
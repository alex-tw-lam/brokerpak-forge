"""from-jsonschema: input meta-schema validation and conversion."""
import pytest

from forge.spec import check_jsonschema, inputs_from_jsonschema


def test_valid_schema_passes_and_converts():
    doc = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "note": {"type": ["string", "null"]},
            "mode": {"type": "string", "enum": ["a", "b"]},
            "size": {"type": "number", "default": 2},
        },
    }
    check_jsonschema(doc)
    inputs = inputs_from_jsonschema(doc)
    assert inputs["name"].required and inputs["name"].type == "string"
    assert inputs["note"].nullable
    assert inputs["mode"].enum == {"a": "a", "b": "b"}
    assert inputs["size"].default == 2 and inputs["size"].type == "number"


def test_invalid_schema_rejected_with_reason():
    with pytest.raises(ValueError, match="not a valid JSON Schema"):
        check_jsonschema({"type": "string", "properties": 42})


def test_non_object_schema_rejected():
    with pytest.raises(ValueError, match="object schema"):
        check_jsonschema({"type": "array", "items": {"type": "string"}})

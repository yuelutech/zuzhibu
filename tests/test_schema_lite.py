"""schema_lite 的单元测试。"""

import unittest

from shared.backends import schema_lite


class SchemaLiteTest(unittest.TestCase):
    def test_basic_types_and_required(self):
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string"},
                "count": {"type": "integer"},
                "ratio": {"type": "number"},
                "ok": {"type": "boolean"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "count"],
        }
        self.assertEqual(schema_lite.validate({"title": "a", "count": 1}, schema), [])
        errors = schema_lite.validate({"title": 1}, schema)
        self.assertIn("$.title: 应为 string，实际 integer", errors)
        self.assertIn("$: 缺少必填字段 'count'", errors)
        errors = schema_lite.validate({"title": "a", "count": 1, "extra": 2}, schema)
        self.assertEqual(errors, ["$: 多出字段 ['extra']"])
        errors = schema_lite.validate({"title": "a", "count": 1, "tags": ["x", 2]}, schema)
        self.assertEqual(errors, ["$.tags[1]: 应为 string，实际 integer"])

    def test_bool_is_not_integer_and_enum_distinguishes(self):
        self.assertEqual(schema_lite.validate(True, {"type": "integer"}), ["$: 应为 integer，实际 boolean"])
        self.assertEqual(schema_lite.validate(1, {"enum": [True]}), ["$: 不在枚举范围 [True] 内，实际 1"])
        self.assertEqual(schema_lite.validate("speech", {"enum": ["speech", "newswire"]}), [])

    def test_string_and_number_constraints(self):
        self.assertTrue(schema_lite.validate("ab", {"type": "string", "minLength": 3}))
        self.assertTrue(schema_lite.validate("abcd", {"type": "string", "maxLength": 3}))
        self.assertTrue(schema_lite.validate("abc", {"type": "string", "pattern": r"^\d+$"}))
        self.assertEqual(schema_lite.validate("123", {"type": "string", "pattern": r"^\d+$"}), [])
        self.assertTrue(schema_lite.validate(5, {"type": "number", "maximum": 4}))
        self.assertTrue(schema_lite.validate(5, {"type": "number", "exclusiveMaximum": 5}))
        self.assertEqual(schema_lite.validate(5, {"type": "number", "minimum": 5}), [])

    def test_array_constraints(self):
        self.assertTrue(schema_lite.validate([], {"type": "array", "minItems": 1}))
        self.assertTrue(schema_lite.validate([1, 2, 3], {"type": "array", "maxItems": 2}))
        self.assertEqual(schema_lite.validate([1, 1], {"type": "array", "uniqueItems": True}), ["$[1]: 与 $[0] 重复"])

    def test_ref_and_combinators(self):
        schema = {
            "$defs": {"pair": {"type": "object", "properties": {"zh": {"type": "string"}}, "required": ["zh"]}},
            "type": "object",
            "properties": {
                "numbers": {"type": "array", "items": {"$ref": "#/$defs/pair"}},
                "mode": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "one": {"oneOf": [{"type": "integer"}, {"type": "string"}]},
                "no": {"not": {"type": "string"}},
            },
        }
        self.assertEqual(schema_lite.validate({"numbers": [{"zh": "一"}], "mode": None, "one": 1, "no": 2}, schema), [])
        errors = schema_lite.validate({"numbers": [{}], "mode": 3, "one": 1.5, "no": "x"}, schema)
        self.assertIn("$.numbers[0]: 缺少必填字段 'zh'", errors)
        self.assertIn("$.mode: 不满足 anyOf 的任何一个分支", errors)
        self.assertIn("$.one: oneOf 应恰好满足一个分支，实际满足 0 个", errors)
        self.assertIn("$.no: 不应满足 not 分支", errors)

    def test_bad_schema_raises(self):
        with self.assertRaises(schema_lite.SchemaError):
            schema_lite.validate(1, {"type": "integer", "$ref": "#/missing"})
        with self.assertRaises(schema_lite.SchemaError):
            schema_lite.validate(1, {"type": "whatever"})

    def test_bureau_schemas_accept_fixture_responses(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        fixtures = Path(__file__).resolve().parent / "fixtures"
        for bureau, fixture in (("secretariat", "speech_response.json"), ("publicity", "newswire_response.json")):
            schema = json.loads((root / "bureaus" / bureau / "output.schema.json").read_text(encoding="utf-8"))
            data = json.loads((fixtures / fixture).read_text(encoding="utf-8"))
            self.assertEqual(schema_lite.validate(data, schema), [], bureau)
            self.assertNotEqual(schema_lite.validate({"unexpected": True}, schema), [], bureau)


if __name__ == "__main__":
    unittest.main()

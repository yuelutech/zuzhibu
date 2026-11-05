"""schema_lite：标准库实现的 JSON Schema 子集校验。

用途是检查模型后端返回的结构化输出是否符合我们给 `--output-schema` 的 schema。
只实现常用关键字，够用即可，不追求完整规范：

- type（单个或列表）、enum、const
- properties、required、additionalProperties（布尔或 schema）
- items（单个 schema）、minItems、maxItems、uniqueItems
- minLength、maxLength、pattern
- minimum、maximum、exclusiveMinimum、exclusiveMaximum
- anyOf、oneOf、allOf、not
- $ref，只支持文档内引用（#/$defs/... 或 #/definitions/...）

不认识的关键字一律忽略。错误信息用 JSON 路径定位，例如 ``$.paragraphs[2]: 应为 string，实际 int``。
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["SchemaError", "validate", "is_valid"]


class SchemaError(ValueError):
    """schema 本身写错了（不是实例不合格）。"""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: _is_int(v) or (isinstance(v, float) and v.is_integer()),
    "number": _is_number,
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _same(a: Any, b: Any) -> bool:
    """enum/const/uniqueItems 用的相等：区分 True 与 1、False 与 0。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return a == b


def _resolve_ref(ref: str, root: Any) -> Any:
    if not isinstance(ref, str) or not ref.startswith("#"):
        raise SchemaError(f"只支持文档内引用，收到 {ref!r}")
    node = root
    pointer = ref[1:]
    if pointer.startswith("/"):
        pointer = pointer[1:]
    if pointer == "":
        return node
    for raw in pointer.split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError) as exc:
                raise SchemaError(f"引用不存在：{ref}") from exc
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise SchemaError(f"引用不存在：{ref}")
    return node


def validate(instance: Any, schema: Any, root: Any = None) -> list[str]:
    """校验实例，返回错误信息列表；空列表表示通过。"""
    errors: list[str] = []
    _validate(instance, schema, "$", schema if root is None else root, errors)
    return errors


def is_valid(instance: Any, schema: Any) -> bool:
    return not validate(instance, schema)


def _validate(inst: Any, schema: Any, path: str, root: Any, errors: list[str]) -> None:
    if schema is True:
        return
    if schema is False:
        errors.append(f"{path}: 不允许任何值")
        return
    if not isinstance(schema, dict):
        raise SchemaError(f"{path}: schema 必须是对象或布尔，收到 {type(schema).__name__}")

    if "$ref" in schema:
        _validate(inst, _resolve_ref(schema["$ref"], root), path, root, errors)
        return

    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        for t in types:
            if t not in _TYPE_CHECKS:
                raise SchemaError(f"{path}: 不认识的 type {t!r}")
        if not any(_TYPE_CHECKS[t](inst) for t in types):
            errors.append(f"{path}: 应为 {'/'.join(types)}，实际 {_type_name(inst)}")
            return

    if "enum" in schema and not any(_same(inst, cand) for cand in schema["enum"]):
        errors.append(f"{path}: 不在枚举范围 {schema['enum']!r} 内，实际 {inst!r}")
    if "const" in schema and not _same(inst, schema["const"]):
        errors.append(f"{path}: 应等于 {schema['const']!r}，实际 {inst!r}")

    if isinstance(inst, str):
        if "minLength" in schema and len(inst) < schema["minLength"]:
            errors.append(f"{path}: 长度 {len(inst)} 小于 minLength {schema['minLength']}")
        if "maxLength" in schema and len(inst) > schema["maxLength"]:
            errors.append(f"{path}: 长度 {len(inst)} 大于 maxLength {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], inst) is None:
            errors.append(f"{path}: 不匹配 pattern {schema['pattern']!r}")

    if _is_number(inst):
        if "minimum" in schema and inst < schema["minimum"]:
            errors.append(f"{path}: {inst} 小于 minimum {schema['minimum']}")
        if "maximum" in schema and inst > schema["maximum"]:
            errors.append(f"{path}: {inst} 大于 maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and inst <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: {inst} 不大于 exclusiveMinimum {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and inst >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: {inst} 不小于 exclusiveMaximum {schema['exclusiveMaximum']}")

    if isinstance(inst, list):
        if "minItems" in schema and len(inst) < schema["minItems"]:
            errors.append(f"{path}: 元素数 {len(inst)} 小于 minItems {schema['minItems']}")
        if "maxItems" in schema and len(inst) > schema["maxItems"]:
            errors.append(f"{path}: 元素数 {len(inst)} 大于 maxItems {schema['maxItems']}")
        if schema.get("uniqueItems"):
            for i in range(len(inst)):
                for j in range(i):
                    if _same(inst[i], inst[j]):
                        errors.append(f"{path}[{i}]: 与 {path}[{j}] 重复")
                        break
        items = schema.get("items")
        if isinstance(items, (dict, bool)):
            for i, element in enumerate(inst):
                _validate(element, items, f"{path}[{i}]", root, errors)
        elif isinstance(items, list):
            for i, (element, sub) in enumerate(zip(inst, items)):
                _validate(element, sub, f"{path}[{i}]", root, errors)

    if isinstance(inst, dict):
        for key in schema.get("required", []):
            if key not in inst:
                errors.append(f"{path}: 缺少必填字段 {key!r}")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in inst:
                _validate(inst[key], sub, f"{path}.{key}", root, errors)
        additional = schema.get("additionalProperties", True)
        extra = [k for k in inst if k not in props]
        if additional is False and extra:
            errors.append(f"{path}: 多出字段 {sorted(extra)!r}")
        elif isinstance(additional, dict):
            for key in extra:
                _validate(inst[key], additional, f"{path}.{key}", root, errors)

    if "allOf" in schema:
        for i, sub in enumerate(schema["allOf"]):
            _validate(inst, sub, path, root, errors)
    if "anyOf" in schema:
        if not any(not validate(inst, sub, root) for sub in schema["anyOf"]):
            errors.append(f"{path}: 不满足 anyOf 的任何一个分支")
    if "oneOf" in schema:
        hits = sum(1 for sub in schema["oneOf"] if not validate(inst, sub, root))
        if hits != 1:
            errors.append(f"{path}: oneOf 应恰好满足一个分支，实际满足 {hits} 个")
    if "not" in schema and not validate(inst, schema["not"], root):
        errors.append(f"{path}: 不应满足 not 分支")

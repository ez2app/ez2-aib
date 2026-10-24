# SPDX-License-Identifier: Apache-2.0
"""最小 JSON Schema 2020-12 子集直譯器 —— 為事件帳本的 fail-closed 驗證而生。

存在理由:本機沒有 jsonschema 套件,而帳本驗證不能因環境缺件而靜默跳過
(那正是舊載體半年圍牆史的病根)。schema 檔案(schemas/event.schema.json)
仍是唯一正本;本模組只是直譯器,不是第二份真相。

fail-closed 紀律:
  1. 遇到本子集未實作的 keyword,一律 raise,絕不靜默當通過 ——
     schema 若演化用了新 keyword,帳本會停止收件而不是收壞件。
  2. 驗證錯誤帶 JSON 路徑,供投影/人類定位。

子集涵蓋(= 三份 AIB schema 實際用到的全部 keyword):
  $ref(僅站內 #/ 指標)、type、properties、required、additionalProperties、
  const、enum、pattern、minLength、maxLength、minimum、maximum、
  minItems、maxItems、items、oneOf、allOf、if/then
"""

import re

# 純註解性 keyword,驗證時忽略
_ANNOTATIONS = {
    "$schema", "$id", "title", "description", "$defs",
    "default", "deprecated", "examples",
}

_SUPPORTED = {
    "$ref", "type", "properties", "required", "additionalProperties",
    "const", "enum", "pattern", "minLength", "maxLength",
    "minimum", "maximum", "minItems", "maxItems", "items",
    "oneOf", "allOf", "if", "then", "else", "not",
}


class SchemaValidationError(ValueError):
    """實例不合 schema,或 schema 本身超出本子集(兩者都必須擋下)。"""

    def __init__(self, path, message):
        self.path = path or "$"
        self.message = message
        super().__init__("%s: %s" % (self.path, message))


def _type_ok(inst, t):
    # Python 的 bool 是 int 子類;JSON Schema 的 integer/number 不含 boolean
    if t == "object":
        return isinstance(inst, dict)
    if t == "array":
        return isinstance(inst, list)
    if t == "string":
        return isinstance(inst, str)
    if t == "integer":
        return isinstance(inst, int) and not isinstance(inst, bool)
    if t == "number":
        return isinstance(inst, (int, float)) and not isinstance(inst, bool)
    if t == "boolean":
        return isinstance(inst, bool)
    if t == "null":
        return inst is None
    raise SchemaValidationError(None, "unsupported type keyword value: %r" % (t,))


def _json_eq(a, b):
    # const/enum 用 JSON 型別語意深度比較；Python 原生 == 會把巢狀的
    # [True] 與 [1] 視為相等，不能拿來守 schema 邊界。
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, list) or isinstance(b, list):
        return isinstance(a, list) and isinstance(b, list) \
            and len(a) == len(b) \
            and all(_json_eq(left, right)
                    for left, right in zip(a, b))
    if isinstance(a, dict) or isinstance(b, dict):
        return isinstance(a, dict) and isinstance(b, dict) \
            and set(a) == set(b) \
            and all(_json_eq(a[key], b[key]) for key in a)
    return a == b


class SchemaValidator:
    def __init__(self, root_schema):
        if not isinstance(root_schema, dict):
            raise SchemaValidationError(None, "root schema must be an object")
        self._root = root_schema

    def validate(self, instance):
        """驗證通過回 None;失敗 raise SchemaValidationError。"""
        self._check(instance, self._root, "$")

    # -- internal ----------------------------------------------------------

    def _resolve(self, ref, path):
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise SchemaValidationError(path, "unsupported $ref target: %r" % (ref,))
        node = self._root
        for raw in ref[2:].split("/"):
            key = raw.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or key not in node:
                raise SchemaValidationError(path, "unresolvable $ref: %s" % ref)
            node = node[key]
        return node

    def _passes(self, inst, schema, path):
        try:
            self._check(inst, schema, path)
            return True
        except SchemaValidationError as err:
            # schema 本身的問題(unsupported keyword / 壞 $ref)不能被 oneOf/if 吞掉
            if err.message.startswith("unsupported") or err.message.startswith("unresolvable"):
                raise
            return False

    def _check(self, inst, schema, path):
        if not isinstance(schema, dict):
            raise SchemaValidationError(path, "unsupported schema form (must be object)")

        for key in schema:
            if key not in _SUPPORTED and key not in _ANNOTATIONS:
                raise SchemaValidationError(path, "unsupported keyword: %s" % key)

        if "$ref" in schema:
            self._check(inst, self._resolve(schema["$ref"], path), path)

        if "type" in schema:
            t = schema["type"]
            types = t if isinstance(t, list) else [t]
            if not any(_type_ok(inst, x) for x in types):
                raise SchemaValidationError(
                    path, "expected type %s, got %s" % (types, type(inst).__name__))

        if "const" in schema:
            if not _json_eq(inst, schema["const"]):
                raise SchemaValidationError(path, "must equal const %r" % (schema["const"],))

        if "enum" in schema:
            if not any(_json_eq(inst, v) for v in schema["enum"]):
                raise SchemaValidationError(path, "%r not in enum" % (inst,))

        if isinstance(inst, str):
            if "pattern" in schema and re.search(schema["pattern"], inst) is None:
                raise SchemaValidationError(path, "does not match pattern %s" % schema["pattern"])
            if "minLength" in schema and len(inst) < schema["minLength"]:
                raise SchemaValidationError(path, "shorter than minLength %d" % schema["minLength"])
            if "maxLength" in schema and len(inst) > schema["maxLength"]:
                raise SchemaValidationError(path, "longer than maxLength %d" % schema["maxLength"])

        if isinstance(inst, (int, float)) and not isinstance(inst, bool):
            if "minimum" in schema and inst < schema["minimum"]:
                raise SchemaValidationError(path, "below minimum %s" % schema["minimum"])
            if "maximum" in schema and inst > schema["maximum"]:
                raise SchemaValidationError(path, "above maximum %s" % schema["maximum"])

        if isinstance(inst, list):
            if "minItems" in schema and len(inst) < schema["minItems"]:
                raise SchemaValidationError(path, "fewer than minItems %d" % schema["minItems"])
            if "maxItems" in schema and len(inst) > schema["maxItems"]:
                raise SchemaValidationError(path, "more than maxItems %d" % schema["maxItems"])
            if "items" in schema:
                for i, item in enumerate(inst):
                    self._check(item, schema["items"], "%s[%d]" % (path, i))

        if isinstance(inst, dict):
            props = schema.get("properties", {})
            for name in schema.get("required", []):
                if name not in inst:
                    raise SchemaValidationError(path, "missing required property %r" % name)
            for name, sub in props.items():
                if name in inst:
                    self._check(inst[name], sub, "%s.%s" % (path, name))
            if "additionalProperties" in schema:
                ap = schema["additionalProperties"]
                extras = [k for k in inst if k not in props]
                if ap is False:
                    if extras:
                        raise SchemaValidationError(
                            path, "additional properties not allowed: %s" % sorted(extras))
                elif isinstance(ap, dict):
                    for k in extras:
                        self._check(inst[k], ap, "%s.%s" % (path, k))
                else:
                    raise SchemaValidationError(path, "unsupported additionalProperties form")

        if "oneOf" in schema:
            hits = sum(1 for sub in schema["oneOf"] if self._passes(inst, sub, path))
            if hits != 1:
                raise SchemaValidationError(
                    path, "must match exactly one of oneOf (matched %d)" % hits)

        if "allOf" in schema:
            for i, sub in enumerate(schema["allOf"]):
                self._check(inst, sub, path)

        if "if" in schema:
            if self._passes(inst, schema["if"], path):
                if "then" in schema:
                    self._check(inst, schema["then"], path)
            elif "else" in schema:
                self._check(inst, schema["else"], path)

        if "not" in schema:
            if self._passes(inst, schema["not"], path):
                raise SchemaValidationError(path, "must not match the 'not' subschema")

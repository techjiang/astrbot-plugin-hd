"""``_conf_schema.json`` 的结构契约。

WebUI 的配置面板完全由这份 schema 驱动，它一旦写坏，插件本身能跑、
但**配置面板会静默丢项**（用户在 WebUI 里看不到某个开关，于是以为
功能不存在）。所以这里对结构做静态断言，而不是只保证「是合法 JSON」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "_conf_schema.json"
# 取值的依据是 AstrBot 配置校验器实际识别的分支
# （``astrbot/dashboard/routes/config.py`` 的 validate 函数），
# 不是凭空定的白名单 —— 写漏会让测试误报，写多会放过真错误。
VALID_TYPES = {
    "bool",
    "int",
    "float",
    "string",
    "list",
    "object",
    "text",
    "file",
    "template_list",
}


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _walk_items(node: dict, path: str = ""):
    """递归遍历 type=object 的 items，产出 ``(路径, 定义)``。"""
    for key, value in node.items():
        if not isinstance(value, dict):
            raise AssertionError(f"{path}.{key} 的值不是对象")
        yield f"{path}.{key}".lstrip("."), value
        if value.get("type") == "object":
            items = value.get("items")
            if isinstance(items, dict):
                yield from _walk_items(items, f"{path}.{key}".lstrip("."))


def test_schema_is_a_json_object(schema):
    assert isinstance(schema, dict)
    assert schema, "schema 不能为空"


def test_top_level_groups_are_objects(schema):
    for name, group in schema.items():
        assert isinstance(group, dict), f"{name} 不是对象"
        assert "description" in group, f"{name} 缺少 description"
        assert "type" in group, f"{name} 缺少 type"


def test_every_field_has_description_and_type(schema):
    for path, field in _walk_items(schema):
        assert "description" in field, f"{path} 缺少 description"
        assert "type" in field, f"{path} 缺少 type"


def test_every_type_is_recognized(schema):
    bad = [p for p, f in _walk_items(schema) if f.get("type") not in VALID_TYPES]
    assert not bad, f"以下字段的 type 不被 AstrBot 识别：{bad}"


def test_object_groups_declare_items(schema):
    """``type: object`` 必须带 ``items``，否则 WebUI 渲染成空分组。"""
    bad = []
    for path, field in _walk_items(schema):
        if field.get("type") == "object" and not isinstance(field.get("items"), dict):
            bad.append(path)
    assert not bad, f"以下 object 分组缺少 items：{bad}"


def test_enabled_switch_defaults_to_bool(schema):
    """所有 ``enabled`` 开关都必须是 bool 且给默认值。

    漏给默认值会让功能在用户没碰过配置时处于「未定义」，表现为开关随机。
    """
    bad = []
    for path, field in _walk_items(schema):
        if path.rsplit(".", 1)[-1] != "enabled":
            continue
        if field.get("type") != "bool" or not isinstance(field.get("default"), bool):
            bad.append(path)
    assert not bad, f"以下 enabled 开关定义不合法：{bad}"


def test_int_fields_have_defaults(schema):
    for path, field in _walk_items(schema):
        if field.get("type") in ("int", "float"):
            assert "default" in field, f"{path} 是数值项但没有默认值"


def test_new_v130_groups_present(schema):
    """v1.3.0 新增的三个分组必须在 schema 里可配。"""
    for name in ("boss", "season", "pet"):
        assert name in schema, f"缺少配置分组 {name}"
        assert "enabled" in schema[name]["items"], f"{name} 缺少 enabled 开关"


def test_help_documented_groups_match_schema(schema):
    """README 里列出的分组名必须都在 schema 里存在。

    这条防的是「文档写了但配置项不存在」——用户照着文档找一圈找不到。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [name for name in schema if f"`{name}`" not in readme]
    assert not missing, f"以下配置分组没有在 README 里出现：{missing}"

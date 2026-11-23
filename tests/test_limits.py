"""功能使用次数限制（v1.7.0）回归测试。

核心诉求来自运营反馈：有人靠「数字破解」一下午刷到 33000 分并刷屏，
把经济系统和正常聊天都冲垮了。调奖励数值压不住，因此引入按功能、
按人、按天的次数上限。这里覆盖：默认不限制、上限生效、赌博玩法豁免、
管理员豁免、跨天清零、各功能键已接线、schema 一致。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrbot_plugin_hudong import main as plugin_main  # noqa: E402

# 复用 test_main 的事件构造器与默认配置，避免重复造轮子
from tests.test_main import DEFAULT_CONFIG, make_event  # noqa: E402


@pytest.fixture()
def plugin(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_main, "resolve_data_dir", lambda: tmp_path / "data")
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["limits"] = {"enabled": True}
    return plugin_main.InteractionPlugin(context=None, config=cfg)


def _set(cfg: dict, feature: str, daily: int, enabled: bool = True) -> None:
    cfg.setdefault("limits", {})[feature] = {"enabled": enabled, "daily": daily}


# --------------------------------------------------------------- 核心行为


def test_default_no_limit(plugin):
    """未配置 limits 时全部不限制，行为与旧版一致。"""
    for _ in range(30):
        assert plugin._usage_check(make_event("破解"), "codebreaker") is None


def test_daily_cap_enforced(plugin):
    """上限生效：用完即拒绝，且拒绝理由含功能名与上限。"""
    _set(plugin.config, "codebreaker", 3)
    for _ in range(3):
        assert plugin._usage_check(make_event("破解"), "codebreaker") is None
    deny = plugin._usage_check(make_event("破解"), "codebreaker")
    assert deny is not None
    assert "数字破解" in deny
    assert "3" in deny


def test_limit_is_per_feature(plugin):
    """各功能独立计数，互不干扰。"""
    _set(plugin.config, "codebreaker", 1)
    _set(plugin.config, "guess", 1)
    assert plugin._usage_check(make_event("破解"), "codebreaker") is None
    assert plugin._usage_check(make_event("破解"), "codebreaker") is not None
    assert plugin._usage_check(make_event("猜数字"), "guess") is None
    assert plugin._usage_check(make_event("猜数字"), "guess") is not None


def test_limit_is_per_user(plugin):
    """按用户隔离：一个人用完不影响另一个人。"""
    _set(plugin.config, "codebreaker", 1)
    assert plugin._usage_check(make_event("破解", uid="1001"), "codebreaker") is None
    assert (
        plugin._usage_check(make_event("破解", uid="1001"), "codebreaker") is not None
    )
    assert plugin._usage_check(make_event("破解", uid="1002"), "codebreaker") is None


def test_limit_is_per_session(plugin):
    """按会话隔离：不同群各自计数。"""
    _set(plugin.config, "codebreaker", 1)
    assert plugin._usage_check(make_event("破解", gid="g1"), "codebreaker") is None
    assert plugin._usage_check(make_event("破解", gid="g1"), "codebreaker") is not None
    assert plugin._usage_check(make_event("破解", gid="g2"), "codebreaker") is None


def test_switch_off_disables_limit(plugin):
    """功能开关关闭时，即使 daily 设了值也不限制。"""
    _set(plugin.config, "codebreaker", 1, enabled=False)
    for _ in range(5):
        assert plugin._usage_check(make_event("破解"), "codebreaker") is None


def test_master_switch_off_disables_all(plugin):
    """总开关关闭时所有限制失效。"""
    _set(plugin.config, "codebreaker", 1)
    plugin.config["limits"]["enabled"] = False
    for _ in range(5):
        assert plugin._usage_check(make_event("破解"), "codebreaker") is None


def test_cross_day_resets(plugin, monkeypatch):
    """跨天自动清零。"""
    _set(plugin.config, "codebreaker", 1)
    assert plugin._usage_check(make_event("破解"), "codebreaker") is None
    assert plugin._usage_check(make_event("破解"), "codebreaker") is not None
    # 伪造第二天的日期
    monkeypatch.setattr(plugin_main.time, "strftime", lambda fmt: "2099-01-01")
    assert plugin._usage_check(make_event("破解"), "codebreaker") is None


# --------------------------------------------------------------- 豁免


def test_gambling_features_never_limited(plugin):
    """赌博 / 回收型玩法不参与限制（用户明确要求）。"""
    for feature in plugin_main.GAMBLING_FEATURES:
        _set(plugin.config, feature, 1)
        for _ in range(5):
            assert plugin._usage_check(make_event("x"), feature) is None, feature


def test_admin_exempt(plugin):
    """群主 / 全局管理员豁免。"""
    _set(plugin.config, "codebreaker", 1)
    admin_event = make_event("破解", admin=True)
    for _ in range(5):
        assert plugin._usage_check(admin_event, "codebreaker") is None


def test_config_admin_id_exempt(plugin):
    """permission.admin_ids 里的用户豁免。"""
    _set(plugin.config, "codebreaker", 1)
    plugin.config["permission"]["admin_ids"] = ["1001"]
    for _ in range(5):
        assert (
            plugin._usage_check(make_event("破解", uid="1001"), "codebreaker") is None
        )
    assert plugin._usage_check(make_event("破解", uid="1002"), "codebreaker") is None
    assert (
        plugin._usage_check(make_event("破解", uid="1002"), "codebreaker") is not None
    )


# --------------------------------------------------------------- 接线检查


def test_every_label_has_schema_entry():
    """FEATURE_LABELS 的每个键都必须在 schema 的 limits 里可配。"""
    import json

    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "_conf_schema.json").read_text(
            encoding="utf-8"
        )
    )
    limit_keys = set(schema["limits"]["items"]) - {"enabled"}
    assert limit_keys == set(plugin_main.FEATURE_LABELS), (
        "schema 与 FEATURE_LABELS 不一致：",
        limit_keys ^ set(plugin_main.FEATURE_LABELS),
    )


def test_no_gambling_feature_in_labels():
    """赌博玩法不能出现在受限功能表里。"""
    overlap = plugin_main.GAMBLING_FEATURES & set(plugin_main.FEATURE_LABELS)
    assert not overlap, overlap


def test_all_limited_handlers_are_wired(plugin):
    """每个受限功能至少有一个 handler 调用了 _usage_check。"""
    import inspect

    src = inspect.getsource(plugin_main.InteractionPlugin)
    for feature in plugin_main.FEATURE_LABELS:
        assert f'_usage_check(event, "{feature}")' in src, feature

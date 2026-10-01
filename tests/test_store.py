"""store.py 存储层单元测试。

覆盖字段补齐、并发安全、节流落盘、原子写入、淘汰与容错。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from store import USER_FIELDS, InteractionStore  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path) -> InteractionStore:
    return InteractionStore(tmp_path / "data")


def test_get_user_fills_all_fields(store: InteractionStore):
    user = store.get_user("g1", "u1")
    assert set(USER_FIELDS).issubset(user)
    assert user["balance"] == 0


def test_get_user_is_stable_across_calls(store: InteractionStore):
    a = store.get_user("g1", "u1")
    a["balance"] = 42
    b = store.get_user("g1", "u1")
    assert b["balance"] == 42 and a is b


def test_add_balance_never_negative(store: InteractionStore):
    store.add_balance("g1", "u1", 10)
    assert store.add_balance("g1", "u1", -999) == 0


def test_transfer_success_and_guards(store: InteractionStore):
    store.add_balance("g1", "a", 100)
    ok, _ = store.transfer("g1", "a", "b", 30)
    assert ok and store.peek_user("g1", "a")["balance"] == 70
    assert store.peek_user("g1", "b")["balance"] == 30

    assert not store.transfer("g1", "a", "a", 1)[0]
    assert not store.transfer("g1", "a", "b", 0)[0]
    assert not store.transfer("g1", "a", "b", 10**6)[0]


def test_sessions_are_isolated(store: InteractionStore):
    store.add_balance("g1", "u1", 100)
    store.add_balance("g2", "u1", 7)
    assert store.peek_user("g1", "u1")["balance"] == 100
    assert store.peek_user("g2", "u1")["balance"] == 7


def test_top_users_sorted_and_filtered(store: InteractionStore):
    for uid, bal in (("a", 10), ("b", 300), ("c", 0), ("d", 50)):
        store.add_balance("g1", uid, bal)
    rows = store.top_users("g1", "balance", 10)
    assert [r[0] for r in rows] == ["b", "d", "a"]  # 0 分不入选


def test_display_name_falls_back_to_id(store: InteractionStore):
    store.get_user("g1", "u1")
    assert store.display_name("g1", "u1") == "u1"
    store.rename("g1", "u1", "小明")
    assert store.display_name("g1", "u1") == "小明"


def test_reset_user(store: InteractionStore):
    store.add_balance("g1", "u1", 500)
    store.reset_user("g1", "u1")
    assert store.peek_user("g1", "u1")["balance"] == 0


@pytest.mark.asyncio
async def test_save_load_roundtrip(store: InteractionStore, tmp_path: Path):
    store.add_balance("g1", "u1", 88)
    await store.save("g1", force=True)
    peeked = store.peek_user("g1", "u1")
    assert peeked is not None

    fresh = InteractionStore(tmp_path / "data")
    assert fresh.peek_user("g1", "u1")["balance"] == 88


@pytest.mark.asyncio
async def test_atomic_write_leaves_no_tmp_files(store: InteractionStore):
    store.add_balance("g1", "u1", 1)
    await store.save("g1", force=True)
    assert not list(store.data_dir.glob(".tmp_*"))
    files = list(store.data_dir.glob("*.json"))
    assert len(files) == 1
    json.loads(files[0].read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_corrupt_file_recovers(store: InteractionStore):
    store.add_balance("g1", "u1", 5)
    await store.save("g1", force=True)
    path = next(store.data_dir.glob("*.json"))
    path.write_text("{ not json", encoding="utf-8")

    fresh = InteractionStore(store.data_dir)
    assert fresh.peek_user("g1", "u1") is None
    assert fresh.get_user("g1", "u1")["balance"] == 0


@pytest.mark.asyncio
async def test_weird_session_names_are_sanitized(store: InteractionStore):
    store.add_balance("../../etc/passwd", "u1", 1)
    await store.save("../../etc/passwd", force=True)
    assert all(p.parent == store.data_dir for p in store.data_dir.glob("*.json"))


@pytest.mark.asyncio
async def test_flush_all_writes_dirty_sessions(store: InteractionStore):
    for i in range(5):
        store.add_balance(f"g{i}", "u1", i + 1)
    await store.flush_all()
    assert len(list(store.data_dir.glob("*.json"))) == 5


@pytest.mark.asyncio
async def test_poll_pruning(store: InteractionStore):
    now = 1_000_000.0
    store.add_poll("g1", {"id": "old", "started_at": now - 100_000})
    store.add_poll("g1", {"id": "new", "started_at": now - 10})
    assert store.prune_polls(86400, now) == 1
    assert set(store.polls("g1")) == {"new"}


@pytest.mark.asyncio
async def test_stats_reports_cache(store: InteractionStore):
    store.add_balance("g1", "u1", 1)
    store.add_balance("g1", "u2", 1)
    stats = store.stats()
    assert stats["cached_sessions"] == 1 and stats["cached_users"] == 2


@pytest.mark.asyncio
async def test_concurrent_saves_do_not_corrupt(store: InteractionStore):
    for i in range(50):
        store.add_balance("g1", f"u{i}", i + 1)
    await asyncio.gather(*[store.save("g1", force=True) for _ in range(20)])
    files = list(store.data_dir.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert len(data["users"]) == 50


def test_values_of_and_rank_of(store: InteractionStore):
    for uid, bal in (("a", 10), ("b", 300), ("c", 0), ("d", 50)):
        store.add_balance("g1", uid, bal)
    assert sorted(store.values_of("g1", "balance")) == [10, 50, 300]
    assert store.rank_of("g1", "b", "balance") == 1
    assert store.rank_of("g1", "d", "balance") == 2
    assert store.rank_of("g1", "a", "balance") == 3
    # 0 分（不上榜）与不存在的用户都返回 0
    assert store.rank_of("g1", "c", "balance") == 0
    assert store.rank_of("g1", "nobody", "balance") == 0
    assert store.values_of("g1", "nope") == []


def test_rank_of_tolerates_dirty_value(store: InteractionStore):
    store.get_user("g1", "u1")["balance"] = "abc"
    assert store.rank_of("g1", "u1", "balance") == 0


@pytest.mark.asyncio
async def test_written_file_is_group_readable(store: InteractionStore):
    """数据文件权限应是 0640（同组运维可读），不是 mkstemp 的 0600。"""
    import os
    import stat

    store.add_balance("g1", "u1", 1)
    await store.save("g1", force=True)
    path = next(store.data_dir.glob("*.json"))
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o640, oct(mode)


@pytest.mark.asyncio
async def test_close_clears_state(store: InteractionStore):
    store.add_balance("g1", "u1", 5)
    await store.close()
    assert store.stats()["cached_sessions"] == 0
    assert store.stats()["dirty_sessions"] == 0


@pytest.mark.asyncio
async def test_save_is_idempotent_for_clean_session(store: InteractionStore):
    """未标脏的会话不会产生写盘。"""
    store.get_user("g1", "u1")
    await store.save("g1", force=True)
    before = next(store.data_dir.glob("*.json")).read_text(encoding="utf-8")
    await store.save("g1")  # 已落盘，无需再写
    after = next(store.data_dir.glob("*.json")).read_text(encoding="utf-8")
    assert before == after

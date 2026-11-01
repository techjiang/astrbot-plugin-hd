"""转生加成覆盖测试。

历史上这里出过两次问题，都是「漏算」而不是「算错」：

1. v1.3 只有 7 条发奖路径记得调用 ``apply_bonus``，幸运数字、猜谜、抢答、
   知识竞速、每日任务、拔河、宠物这 7 条全漏；
2. 修完第 1 条后，``猜数字`` 的开局预告与实际发放仍走两套算法，
   玩家看到「42」拿到「44」。

所以这组用例做两件事：逐项核对每个发奖点确实吃了加成；核对每个
「预告值」与「实发值」完全相等。反证方式是把 ``_reward`` 改成直通，
这些用例必须整体变红。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrbot_plugin_hudong import games_plus, games_world  # noqa: E402
from astrbot_plugin_hudong import main as plugin_main  # noqa: E402

from tests.test_main import _fresh_config, make_event, run  # noqa: E402

KEY = "aiocqhttp_group_888"
UID = "1001"


@pytest.fixture()
def plugin(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_main, "resolve_data_dir", lambda: tmp_path / "data")
    games_plus.set_bonus_per_level(0.05)
    return plugin_main.InteractionPlugin(context=None, config=_fresh_config())


def prep(plugin, rebirth: int = 0, balance: int = 1000) -> dict:
    """把用户设成指定转生次数，并清掉可能拦住指令的当日标记。

    同时把所有成就标记为「已解锁」：成就奖励会额外改余额，
    混进断言里会让人误以为是转生加成算错（第一次写这组用例就踩了）。
    成就奖励本身固定不吃加成，见 ``test_achievement_reward_is_fixed``。
    """
    user = plugin.store.get_user(KEY, UID)
    user["rebirth"] = rebirth
    user["balance"] = balance
    user["achievements"] = [code for code, *_rest in games_plus.ACHIEVEMENTS]
    user["sign_date"] = ""
    user["streak"] = 0
    user["lucky_last"] = ""
    user["daily_date"] = ""
    return user


def delta(plugin, uid: str = UID) -> int:
    """读当前余额（get_user 每次都会重新同步字段）。"""
    return int(plugin.store.get_user(KEY, uid)["balance"])


# --------------------------------------------------- 政策表本身


def test_bonus_policy_tables_are_disjoint():
    """同一玩法不能既在「吃加成」又在「不吃加成」里。"""
    assert not set(games_plus.BONUS_GAINS) & set(games_plus.BONUS_EXCLUDED)


def test_apply_bonus_respects_policy():
    assert games_plus.apply_bonus(100, 2, "sign") == 110
    assert games_plus.apply_bonus(100, 2, "wheel") == 100
    assert games_plus.apply_bonus(100, 2, "") == 110


def test_bonus_per_level_is_clamped():
    assert games_plus.set_bonus_per_level(0.05) == 0.05
    assert games_plus.set_bonus_per_level(99) == games_plus.REBIRTH_BONUS_PER_LEVEL_MAX
    assert games_plus.set_bonus_per_level(-1) == 0.0
    assert (
        games_plus.set_bonus_per_level("脏数据") == games_plus.REBIRTH_BONUS_PER_LEVEL
    )
    games_plus.set_bonus_per_level(0.05)


def test_plugin_reads_bonus_per_level_from_config(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_main, "resolve_data_dir", lambda: tmp_path / "data")
    cfg = _fresh_config()
    cfg["rebirth_play"] = {"enabled": True, "bonus_per_level": 0.1}
    plugin_main.InteractionPlugin(context=None, config=cfg)
    assert games_plus.bonus_per_level() == 0.1
    assert games_plus.rebirth_bonus(1) == 1.1
    games_plus.set_bonus_per_level(0.05)


def test_bonus_preview_matches_apply():
    """预告文案必须与实发值同源。"""
    assert games_plus.bonus_preview(30, 0, "guess") == "30"
    assert games_plus.bonus_preview(42, 1, "guess") == "42（转生加成后 44）"
    assert games_plus.bonus_preview(42, 1, "wheel") == "42"


# --------------------------------------------------- 每个发奖点都要吃加成


@pytest.mark.asyncio
async def test_sign_reward_gets_bonus_and_counts_season(plugin):
    """签到加成要真的加上，且加成部分也要算进赛季增量。"""
    for rebirth, expect in ((0, 10), (1, 10), (5, 12), (20, 20)):
        prep(plugin, rebirth)
        await run(plugin, "cmd_sign", make_event("签到"))
        assert delta(plugin) - 1000 == expect, (rebirth, expect)

    # 历史上加成部分直接写 balance，绕过了 add_balance，赛季增量漏记
    prep(plugin, 20)
    u = plugin.store.get_user(KEY, UID)
    u["season_gain"] = 0
    await run(plugin, "cmd_sign", make_event("签到"))
    assert plugin.store.get_user(KEY, UID)["season_gain"] == 20


@pytest.mark.asyncio
async def test_lucky_reward_gets_bonus(plugin):
    today = plugin_main.games.today_str()
    lucky = plugin_main.games.lucky_number(today, UID)
    prep(plugin, 1)
    await run(plugin, "cmd_lucky", make_event(f"幸运数字 {lucky}"))
    assert delta(plugin) - 1000 == 15  # 15 × 1.05 = 15.75 → 15
    prep(plugin, 20)
    plugin.store.get_user(KEY, UID)["lucky_last"] = ""
    await run(plugin, "cmd_lucky", make_event(f"幸运数字 {lucky}"))
    assert delta(plugin) - 1000 == 30  # 15 × 2.0


@pytest.mark.asyncio
async def test_riddle_reward_gets_bonus(plugin):
    prep(plugin, 20)
    await run(plugin, "cmd_riddle", make_event("猜谜"))
    answer = plugin._riddles[KEY]["answer"]
    await plugin._riddle_guess(make_event(answer), answer)
    assert delta(plugin) - 1000 == 40  # 20 × 2.0


@pytest.mark.asyncio
async def test_rush_reward_gets_bonus(plugin):
    prep(plugin, 20)
    await run(plugin, "cmd_rush", make_event("抢答 我最快"))
    await plugin._rush_check(make_event("我最快"), "我最快")
    assert delta(plugin) - 1000 == 20  # 10 × 2.0


@pytest.mark.asyncio
async def test_quiz_reward_gets_bonus(plugin):
    prep(plugin, 20)
    await run(plugin, "cmd_quiz", make_event("竞速"))
    game = plugin._quizzes[KEY]
    answer = game.answers[0]
    await plugin._quiz_submit(make_event(answer), game, KEY, UID, "用户1001", answer)
    assert delta(plugin) - 1000 == 50  # 25 × 2.0


@pytest.mark.asyncio
async def test_quest_claim_gets_bonus(plugin):
    user = prep(plugin, 20)
    user["total_sign"] = 1  # 让「完成 1 次签到」任务达成
    user["quest_done"] = []
    user["season_gain"] = 0
    user["balance"] = 1000
    before = delta(plugin)
    out = await run(plugin, "cmd_claim", make_event("领取 sign"))
    assert "完成" in out
    assert delta(plugin) - before == 16  # 8 × 2.0
    assert plugin.store.get_user(KEY, UID)["season_gain"] == 16


@pytest.mark.asyncio
async def test_pet_feed_gets_bonus(plugin, monkeypatch):
    user = prep(plugin, 20)
    user["pet"] = games_world.PetState(
        name="团子", care=0, fed_at=0, born=True
    ).to_dict()
    monkeypatch.setattr(games_world, "pet_gain", lambda care, rng=None: 5)
    await run(plugin, "cmd_pet_feed", make_event("喂食"))
    assert delta(plugin) - 1000 == 10  # 5 × 2.0


@pytest.mark.asyncio
async def test_tug_prize_gets_bonus(plugin):
    prep(plugin, 20)
    await run(plugin, "cmd_tug", make_event("拔河 红队 蓝队 60"))
    await run(plugin, "cmd_join", make_event("加入 红队 100"))
    before = delta(plugin)
    await run(plugin, "cmd_settle", make_event("收工"))
    assert delta(plugin) - before == 100  # 50 × 2.0


@pytest.mark.asyncio
async def test_guess_reward_preview_equals_payout(plugin):
    """预告值与实发值必须一致 —— 这是「后台 30 实际 42」的回归点。"""
    prep(plugin, 3)
    out = await run(plugin, "cmd_guess_start", make_event("猜数字"))
    assert "转生加成后" in out
    announced = int(out.split("转生加成后 ")[1].split("）")[0])
    game = plugin._guesses[KEY]
    before = delta(plugin)
    await plugin._guess_once(make_event(f"猜 {game.target}"), game.target)
    assert delta(plugin) - before == announced


@pytest.mark.asyncio
async def test_lucky_preview_equals_payout(plugin):
    today = plugin_main.games.today_str()
    lucky = plugin_main.games.lucky_number(today, UID)
    prep(plugin, 3)
    out = await run(plugin, "cmd_lucky", make_event("幸运数字"))
    announced = int(out.split("获得 ")[1].split("（转生加成后 ")[1].split("）")[0])
    await run(plugin, "cmd_lucky", make_event(f"幸运数字 {lucky}"))
    assert delta(plugin) - 1000 == announced


# --------------------------------------------------- 不该吃加成的玩法


@pytest.mark.asyncio
async def test_wheel_never_gets_bonus(plugin):
    prep(plugin, 20, balance=100000)
    out = await run(plugin, "cmd_wheel", make_event("转盘"))
    assert "本次净变动" in out


@pytest.mark.asyncio
async def test_coin_and_duel_are_unchanged_by_rebirth(plugin):
    """零和对赌：转生前后的余额变化必须完全相同。"""
    results = []
    for rebirth in (0, 20):
        plugin._rng.seed(7)
        prep(plugin, rebirth)
        await run(plugin, "cmd_coin", make_event("抛硬币 正 100"))
        results.append(delta(plugin))
    assert results[0] == results[1]


@pytest.mark.asyncio
async def test_bonus_command_lists_both_scopes(plugin):
    prep(plugin, 2)
    out = await run(plugin, "cmd_bonus", make_event("我的加成"))
    assert "×1.10" in out
    assert "✅ 吃加成" in out and "⛔ 不吃加成" in out
    for label in ("签到", "猜数字", "知识竞速", "宠物照料"):
        assert label in out
    # 排除项按 key 展示，必须能让玩家认出是哪些玩法
    for key in ("wheel", "lottery", "coin", "boss"):
        assert key in out


@pytest.mark.asyncio
async def test_rebirth_intro_mentions_scope(plugin):
    prep(plugin, 0)
    out = await run(plugin, "cmd_rebirth", make_event("转生"))
    assert "加成生效于" in out


# --------------------------------------------------- 全覆盖守卫


def _balance_writes() -> list[tuple[int, int]]:
    """列出 main.py 里所有 ``user["balance"] = <positive-expr>`` 的行号与常量。

    只关心「往上加钱」的赋值：减法（下注扣除、罚款退还）不属于发奖，
    也不可能造成「少算加成」。
    """
    import ast

    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    writes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "balance"
            ):
                continue
            writes.append((node.lineno, ast.unparse(node.value)))
    return writes


def test_every_reward_path_uses_unified_entry():
    """静态守卫：加成只允许通过 ``_reward``，零和结算只允许通过 ``_grant``。

    历史上这里靠「记得逐个调用 apply_bonus」，于是漏了 7 条路径。
    改动之后，``main.py`` 里除 ``_grant`` 自身外，任何写 ``balance`` 的
    语句都必须落在减法（扣款）或统一入口里，否则直接拦下。
    """
    offenders = []
    for lineno, expr in _balance_writes():
        if " - " in expr and " + " not in expr:
            continue  # 纯扣款，不产生奖励
        if "_reward(" in expr or "_grant(" in expr:
            continue
        if expr == "after":  # _grant 内部
            continue
        offenders.append(f"main.py:{lineno} → {expr}")
    assert not offenders, "存在绕过统一入口的裸加余额：" + "；".join(offenders)


def test_bonus_is_applied_at_most_once():
    """同一个结算点不能既调用 ``_reward`` 又叠加 ``apply_bonus``。"""
    offenders = [
        f"main.py:{lineno} → {expr}"
        for lineno, expr in _balance_writes()
        if "_reward(" in expr and "apply_bonus" in expr
    ]
    assert not offenders


@pytest.mark.asyncio
async def test_achievement_reward_is_fixed(plugin):
    """成就奖励刻意不吃加成：它是一次性里程碑，文案写的是固定值。"""
    user = prep(plugin, 20)
    user["achievements"] = []
    user["balance"] = 0
    user["best_streak"] = 7  # 达成「签到七日」
    plugin._achievement_scan(user)
    from astrbot_plugin_hudong import games_plus as gp

    expected = sum(
        reward
        for _code, _name, field, threshold, reward in gp.ACHIEVEMENTS
        if int(user.get(field, 0) or 0) >= threshold
    )
    assert expected > 0
    assert int(user["balance"]) == expected

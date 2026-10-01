<div align="center">

<img src="assets/banner.png" alt="互动 · AstrBot 群聊互动插件" width="760">

# 互动 · AstrBot 群聊互动插件

**签到 · 积分 · 抽奖 · 猜数字 · 接龙 · 投票 · 排行榜 · 每日任务 · 打劫 · 掷骰 · 幸运数字**

让群聊真正热闹起来的一站式玩法合集

[![version](https://img.shields.io/badge/version-v1.0.0-4f7cff)](./metadata.yaml)
[![astrbot](https://img.shields.io/badge/AstrBot-%3E%3D4.10%2C%20%3C5-22c55e)](https://astrbot.app)
[![python](https://img.shields.io/badge/python-%3E%3D3.10-3776ab)](./pyproject.toml)
[![deps](https://img.shields.io/badge/第三方依赖-0-ff9800)](./requirements.txt)
[![license](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)

[使用文档](./使用文档.md) · [开发改进文档](./开发改进文档.md) · [问题反馈](https://cnb.cool/asoe/TechSauce/astrbot-plugin-hd/-/issues)

</div>

---

## 这是什么

`互动` 是一套给 AstrBot 用的群聊互动玩法合集。装好就能用，不需要数据库、
不需要第三方库、不需要任何初始化 —— 群里第一个签到的人会自动建档。

它解决的是「群太冷清」和「群太吵」这两件事：日常轻玩法拉活跃，
圈层玩法拉留存，娱乐玩法拉话题，同时用冷却、限额和任务给刷屏踩刹车。

## 亮点

- **零第三方依赖** —— 只用 Python 标准库和 `astrbot.api.*`，不会有装不上的包
- **数据会话隔离** —— 按「平台 + 群 / 私聊」分文件，插件升级不丢数据
- **不阻塞事件循环** —— 序列化与写盘都在线程池，`fsync` + 原子替换保证不写坏文件
- **配置可视化** —— 15 个配置分组全部能在 WebUI 里可视化调整
- **数值经得起推敲** —— 抽奖期望、打劫期望都做过收敛，不存在刷分漏洞
- **117 项单测 + 51 项真实框架冒烟** —— 每次改动都会跑

## 快速开始

1. 把仓库克隆/下载到 AstrBot 的 `data/plugins/` 下，目录名保持
   `astrbot_plugin_hudong`（插件源码就在仓库根目录，不要把 `astrbot_plugin_hudong/`
   再套一层）
2. 重启 AstrBot，或在 WebUI 里重载插件
3. 群里发送 `/互动` 查看玩法总览

```
/签到              # 每日签到领积分
/积分              # 余额、等级、称号、统计、今日幸运数字
/每日任务          # 6 个日常任务，发「/领取」一键领奖
/抽奖              # 消耗积分抽奖，含称号与头像框
/猜数字 → /猜 50    # 猜中得积分，范围与次数可配
/接龙 互动          # 直接发中文词语即可接龙
/掷骰 3 20         # 或者直接发 3d20
/幸运数字          # 每天一个专属数字，猜中领奖
/八球 今天要加班吗   # 一句玄学答案
/排行榜 积分        # 积分/签到/抽奖/猜中/接龙/打劫/掷骰/幸运
/投票 晚饭吃啥 | 火锅 | 烧烤
```

完整玩法、参数与示例见 **[使用文档.md](./使用文档.md)**。

## 玩法一览

| 分类 | 指令 | 说明 |
| --- | --- | --- |
| 日常 | `/签到`、`/积分`、`/每日任务`、`/领取` | 签到攒积分，6 个日常任务可领奖 |
| 娱乐 | `/抽奖`、`/猜数字` → `/猜`、`/接龙`、`/掷骰`、`/幸运数字`、`/八球` | 抽取称号、猜数、接龙、掷骰、玄学 |
| 社交 | `/转账`、`/打劫`、`/扎心` | 资产转移与互损 |
| 工具 | `/投票` → `/投` → `/投票结果`、`/排行榜`、`/互动`、`/互动状态` | 投票、榜单、帮助、运行状态 |
| 自动 | 关键词回复 | WebUI 配置关键词与回复内容 |

## 配置

所有配置都在 AstrBot WebUI → 插件配置 里，15 个分组：

`enabled` · `currency_name` · `sign_in` · `lottery` · `guess_number` ·
`word_chain` · `vote` · `rank` · `rob` · `dice` · `lucky` · `eight_ball` ·
`roast` · `auto_reply` · `permission`

默认值开箱即用。打劫与掷骰默认关闭（这两个玩法对群氛围影响较大，
由群主主动开启更合适）；其余玩法默认全开。逐项说明见
[使用文档 · 配置项对照](./使用文档.md#配置项对照)。

## 数据与迁移

数据落在 AstrBot 工作目录下的 `data/astrbot_plugin_hudong/<平台>_<会话>.json`，
一个群一个文件：

```json
{
  "users": { "10001": { "balance": 328, "streak": 3, "...": "..." } },
  "polls": { "a1b2c3": { "question": "晚饭吃啥", "votes": {}, "...": "..." } },
  "meta":  { "created_at": 0, "updated_at": 0 }
}
```

- 整目录拷走即可迁移
- 写入是「节流 3 秒 + 合并 + 卸载强制刷盘」，使用 `mkstemp` → `fsync` → `os.replace`
- 文件权限 `0640`，同组运维账号可读可备份
- 文件损坏会自动重建，不会让插件起不来

## 开发

```bash
pip install ruff pytest pytest-asyncio

ruff format . && ruff check .      # 代码风格
python -m pytest tests -q          # 117 项单元/集成测试
python tools/e2e_smoke.py          # 真实 AstrBot 框架下的 51 项冒烟测试
python tools/build_logo.py assets/design-source.png assets   # 重新生成 logo 资源
```

> `pytest-asyncio` 是可选的 —— `tests/conftest.py` 内置了一个极简的
> `async def` 测试运行钩子，不装也能跑。

架构分层、这轮改进修掉的具体问题、以及如何扩展，见
**[开发改进文档.md](./开发改进文档.md)**。

## Logo

<img src="assets/glyph-128.png" alt="互动" width="72" align="left">

仓库根目录的 `logo.png` 是 AstrBot WebUI 插件列表读取的固定文件名；
`assets/` 下还有多尺寸图标（`logo-*.png`）、纯图形版（`glyph-*.png`）、
favicon、文档横幅（`banner.png`）与社交预览图，全部由 `tools/build_logo.py`
从 `assets/design-source.png` 自动生成，可一键重建：

```bash
python tools/build_logo.py assets/design-source.png assets
```

生成逻辑在 `tools/logo_tools.py`，配套单测见 `tests/test_logo_tools.py`。

<br clear="left">

## 支持的平台

`aiocqhttp` · `qq_official` · `telegram` · `discord` · `lark` · `dingtalk` ·
`slack` · `kook` · `satori`

数据按平台隔离，同一个用户在不同平台互不影响。

## 作者

**科技酱**

- 网站：<https://docs.asoe.cn>
- GitHub：<https://github.com/techjiang/>
- B 站：<https://space.bilibili.com/1768832152>
- 论坛：<https://forums.asoe.cn/>
- QQ 群：291974598（② 474819022）

## License

[MIT](./LICENSE)

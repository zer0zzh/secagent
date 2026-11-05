# 阶段1 · 03 任务拆解与版本控制

> 拆解原则：每个任务**能独立运行、能独立验证**，失败时只回滚这一个提交。
> 顺序不允许跳；T(n) 的验收命令没通过，不动 T(n+1)。

## 任务清单

| # | 任务 | 产出 | 独立验收命令 | 提交信息 |
|---|---|---|---|---|
| T0 | 阶段0 基线归档 | 现有可运行代码进 Git | `python -m agent.cli probe` 返回 `状态 ok` | `chore: baseline phase0 (ReAct loop + trace)` |
| T1 | 数据层：SQLite 建表 + trace 导入 | `agent/store.py`、`tests/test_store.py` | `python -m unittest discover -s tests -v` 全绿；`python -m agent.store import <trace>` 能查到 runs/steps | `feat(store): sqlite schema + trace importer` |
| T2 | 工具扩展：`dir_enum` | `tools/registry.py` 新增工具 | `python -m agent.tools.selftest` 打印目录探测结果 | `feat(tools): add dir_enum` |
| T3 | 工具扩展：`payload_verify` | 三次复现 + 响应差异 | 单元测：同 payload 三次返回一致 → diff 可判定 | `feat(tools): add payload_verify with repeat=3` |
| T4 | 证据链接入循环 | `run.py` 把 tool_result 写进 `evidence` | 跑一次 Demo，DB 里 `evidence` 行数 ≥ tool_calls | `feat(run): persist evidence chain` |
| T5 | verifier 三类断言 | `agent/verifier.py` | 单测：正例通过、静态 200 页被拒、延迟 <4s 不算时间盲注 | `feat(verifier): response_diff / error_echo / time_delay` |
| T6 | 结论闸门 | `verdicts` 只由 verifier 产生 | 构造一条无证据的模型结论，必须落 `UNVERIFIED` | `feat(verdict): reject evidence-free conclusions` |
| T7 | 评测集 12 题 | `eval/cases.jsonl` | `python -m agent.eval --list` 输出 6 正 6 负 | `feat(eval): 12 cases (6 positive / 6 negative)` |
| T8 | 评测报告 | `eval/run_eval.py` → `eval/report.md` | 报告含成功率、误报率、步数、耗时、token 五列真实数字 | `feat(eval): metrics report` |
| T9 | 一键演示与打包 | `demo.bat` 增强 + 架构图 + 一页纸 | 断网状态下 `demo.bat` 跑完 ≤90 秒 | `chore(demo): one-click offline demo` |

## 依赖关系（不能并行的地方）

```
T0 ──> T1 ──> T4 ──> T6 ──> T8 ──> T9
              ▲      ▲      ▲
              │      │      └── T7（评测集，可与 T5 并行准备）
              │      └───────── T5
              └──────────────── T2 ──> T3
```

- T2/T3 只碰工具层，可与 T1 并行，但**先做 T1**：没有证据模型，工具返回存不进去。
- T5 必须等 T3（`payload_verify` 提供三次复现数据）。
- T8 必须等 T6 + T7。

## Git 规则

1. 一个任务 = 一个提交；提交信息格式 `type(scope): 一句话`。
2. 提交前必须跑该任务的验收命令，失败不提交。
3. `.env`、`runs/`、`data/*.db`、`__pycache__/` 全部忽略，密钥不进历史。
4. 每次提交后把提交哈希记在 `docs/phase1/04-implementation-log.md`，出问题时按哈希回退。
5. 不建分支、不搞 PR：单人项目，main 一条线，够用。

## 当前进度

- [x] T0 — 阶段0 基线（2026-11-05 09:1x）
- [x] T1 — 数据层 + 单测（本轮完成，见 04-implementation-log.md）
- [ ] T2 ~ T9

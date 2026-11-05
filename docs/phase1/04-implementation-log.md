# 阶段1 · 04 编码与自测日志

> 规则：每个任务只记"验收命令 + 实际输出 + 提交哈希"。没跑通过的，不记完成。

## T0 · 阶段0 基线

- 提交：`fa0da1f` — `chore: baseline phase0 (ReAct loop + trace) and phase1 design docs`
- 验收：`python -m agent.cli probe` → `状态 ok`；`python -m agent.run "对 http://127.0.0.1:8080 做一次 HTTP 指纹识别并给出结论"` → 3 步收敛 / 20.8 s
- 纳入版本库：27 个文件；`.env`、`runs/`、`data/`、`__pycache__/` 已忽略

## T1 · 数据层（本轮完成）

| 项 | 内容 |
|---|---|
| 新增 | `agent/schema.sql`（8 张表）、`agent/store.py`（建表 / trace 导入 / summary）、`tests/test_store.py`（7 个单测） |
| 设计依据 | `docs/phase1/02-design.md`，字段未做任何临时改动 |
| 验收 1 | `python -m unittest discover -s tests -v` → **Ran 7 tests in 0.311s, OK** |
| 验收 2 | `python -m agent.store init` → 8 张表 |
| 验收 3 | `python -m agent.store import runs/run-20261105-090111/trace.jsonl` → `steps=3, tool_calls=2, status=converged` |
| 验收 4 | `python -m agent.store summary` → `runs=1, steps=3, tool_calls=2, evidence=0, verdicts=0` |
| 验收 5 | SQL 查询三步：`http_probe/4430ms/40tok`、`port_scan/3183ms/35tok`、`FINAL/10141ms/110tok` |
| 未改动 | `agent/run.py` 一行没碰 → 阶段0 验收命令保持可用 |

单测覆盖点：

1. 8 张表齐全
2. 合成 trace 导入 → runs/steps/tool_calls/final_answer 正确
3. `steps.raw_text` 与 `tool_calls.result_json` 原文保留（证据链的前提）
4. 同一 run_key 重复导入幂等
5. `verdicts.evidence_ids = '[]'` 被 CHECK 约束拒绝（"无证据不结论"落到数据库层）
6. `SECAGENT_DB` 环境变量覆盖生效
7. 用阶段0 的真实 trace 导入，断言 ≥3 步、≥2 次工具调用

## 提交记录

| 哈希 | 任务 | 信息 |
|---|---|---|
| `fa0da1f` | T0 | `chore: baseline phase0 (ReAct loop + trace) and phase1 design docs` |
| `4c42f9e` | T1 | `feat(store): sqlite schema + trace importer + 7 unit tests` |

## 下一任务

T2 `dir_enum`。开工前先确认 `python -m unittest discover -s tests -v` 仍全绿，
完成后再跑一次并单独提交。T4 之前不碰 `agent/run.py`，避免把已验收的循环改坏。

# 阶段1 · 02 设计（先画蓝图）

> 本文件定义"数据长什么样"与"用什么技术"。表结构一旦定下，编码阶段不允许临时加字段；
> 缺字段先回来改这里，评估影响后再动代码。

## 一、数据设计

### 1.1 概念链（一句话）

`run` 一次运行 → 产生 N 个 `step` → 每步可能有 `tool_call` → 工具返回沉淀为 `evidence`
→ 独立 `verifier` 对 claim 做断言 → 产出 `verdict` → 评测时用 `eval_case` / `eval_result` 批量验。

```
run ──< step ──< tool_call ──< evidence
 │                              │
 └──────── verdict >────────────┘       eval_case ──< eval_result ──> run
```

### 1.2 表结构（SQLite，全部 snake_case，时间用 ISO8601 字符串）

```sql
-- 一次完整运行
CREATE TABLE runs (
  id            INTEGER PRIMARY KEY,
  run_key       TEXT UNIQUE NOT NULL,      -- 目录名 run-20261105-090111
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  task          TEXT NOT NULL,
  profile       TEXT NOT NULL,             -- ollama / deepseek / mock
  model         TEXT NOT NULL,
  status        TEXT NOT NULL,             -- converged / step_limit / parse_failed / llm_error
  steps         INTEGER DEFAULT 0,
  tool_calls    INTEGER DEFAULT 0,
  final_answer  TEXT,
  trace_path    TEXT,
  total_tokens  INTEGER DEFAULT 0
);

-- 每一步（含 LLM 输出与解析结果）
CREATE TABLE steps (
  id            INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES runs(id),
  idx           INTEGER NOT NULL,          -- 从 1 开始
  role          TEXT DEFAULT 'agent',     -- agent / guard / system
  thought       TEXT,
  action        TEXT,                     -- 工具名 / FINAL / PARSE_ERROR
  args_json     TEXT,
  raw_text      TEXT,                     -- 模型原文，不裁剪
  latency_ms    INTEGER,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  finish_reason TEXT,
  UNIQUE(run_id, idx)
);

-- 工具调用
CREATE TABLE tool_calls (
  id            INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES runs(id),
  step_id       INTEGER REFERENCES steps(id),
  tool          TEXT NOT NULL,
  args_json     TEXT NOT NULL,
  ok            INTEGER NOT NULL,         -- 0/1
  duration_ms   INTEGER,
  result_json   TEXT,                     -- 完整返回（含截断标记）
  error         TEXT,
  truncated     INTEGER DEFAULT 0
);

-- 证据：把"模型说了什么"和"实际观测到什么"分开存
CREATE TABLE evidence (
  id            INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES runs(id),
  step_id       INTEGER REFERENCES steps(id),
  tool_call_id  INTEGER REFERENCES tool_calls(id),
  kind          TEXT NOT NULL,            -- response_diff / error_echo / time_delay / header / title / port_state
  claim         TEXT NOT NULL,            -- 待验证的断言，如 "存在 SQL 注入"
  subject       TEXT,                     -- URL / 端口 / 参数
  payload_json  TEXT,                     -- 触发它的输入
  observed_json TEXT,                     -- 实际观测
  repeat_count  INTEGER DEFAULT 1,        -- 复现次数（payload_verify 要求 >=3）
  created_at    TEXT NOT NULL
);

-- 独立校验结果：verifier 不看模型结论，只看 evidence
CREATE TABLE verifications (
  id            INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES runs(id),
  claim         TEXT NOT NULL,
  verifier      TEXT NOT NULL,            -- response_diff / error_echo / time_delay
  passed        INTEGER NOT NULL,
  detail        TEXT,
  evidence_ids  TEXT,                     -- JSON array
  created_at    TEXT NOT NULL
);

-- 最终结论
CREATE TABLE verdicts (
  id            INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES runs(id),
  claim         TEXT NOT NULL,
  verdict       TEXT NOT NULL,            -- CONFIRMED / UNVERIFIED / REFUTED
  confidence    REAL,
  evidence_ids  TEXT NOT NULL,            -- 空数组 = 不允许
  model_answer  TEXT,                     -- 模型自评，仅存档，不作准
  human_review  INTEGER DEFAULT 0,
  created_at    TEXT NOT NULL
);

-- 评测集（正 6 负 6）
CREATE TABLE eval_cases (
  id            INTEGER PRIMARY KEY,
  name          TEXT UNIQUE NOT NULL,
  target        TEXT NOT NULL,
  category      TEXT NOT NULL,            -- true_pos / static_200 / waf_block / honeypot
  is_negative   INTEGER NOT NULL,
  expected      TEXT NOT NULL,            -- CONFIRMED / UNVERIFIED / REFUTED
  ground_truth  TEXT,
  notes         TEXT
);

-- 评测结果
CREATE TABLE eval_results (
  id            INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES runs(id),
  case_id       INTEGER NOT NULL REFERENCES eval_cases(id),
  predicted     TEXT NOT NULL,
  correct       INTEGER NOT NULL,
  false_positive INTEGER DEFAULT 0,
  steps         INTEGER,
  latency_ms    INTEGER,
  tokens        INTEGER
);

CREATE INDEX idx_steps_run ON steps(run_id, idx);
CREATE INDEX idx_tc_run ON tool_calls(run_id);
CREATE INDEX idx_ev_run ON evidence(run_id);
CREATE INDEX idx_er_run ON eval_results(run_id);
```

### 1.3 三条数据约束（防止"写到一半发现字段不够"）

1. **`verdicts.evidence_ids` 不允许为 `[]`**：没有证据就不产生结论，退化为 `UNVERIFIED`。
2. **`evidence.observed_json` 只存工具原始返回，不存模型改写过的文本**：否则证据链失效。
3. **`steps.raw_text` 不裁剪**：trace.jsonl 是备份，DB 是查询入口，两处都保留全文。

### 1.4 与现有 trace.jsonl 的关系

| | trace.jsonl | SQLite |
|---|---|---|
| 定位 | 不可变原始流水（append-only） | 可查询、可关联、可评测 |
| 写入时机 | 循环运行时实时写 | 运行结束或手动导入 |
| 是否允许修改 | 否 | 允许补写 verdict / human_review |
| 用途 | 回放、举证、给主办方看 | 出统计表、算误报率 |

两者不互相替代：**trace 是证据原件，DB 是索引**。

## 二、技术选型（只选一套，不混用）

| 层 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.13（现有环境） | 阶段0 已跑通，不重来 |
| LLM | 本机 `qwen2.5:7b-instruct` via ollama `/v1` | 无密钥、可断网、现场稳定 |
| 云端兜底 | `deepseek-chat`（同一份 `openai_compat.py`） | 配置已就绪，填 key 即可，不引入第二套客户端 |
| 存储 | SQLite（`sqlite3` 标准库） | 零运维、单文件、可直接拷给老师；不需要 MySQL 服务 |
| HTTP | `urllib` 标准库 + 显式 `ProxyHandler({})` | 阶段0 已踩过代理坑，不再引第三方 |
| 配置 | `config/llm.yaml` + `.env` | 阶段0 已验证 |
| 测试 | `unittest`（标准库） | 现场不需要装 pytest |
| 展示 | 终端 + Markdown 报告 | 5 分钟能讲完 |
| 版本控制 | Git（本地仓库） | 每任务一提交 |

### 明确不选（并写清为什么，避免中途改主意）

| 不选 | 原因 |
|---|---|
| React/Vue 前端 | 增加构建、部署、Origin 三类问题；培训现场 90 秒内讲不完，且本阶段没有"多用户"需求 |
| MySQL / PostgreSQL | 需要起服务，现场断网/端口冲突风险高；数据量 <1 万行，SQLite 足够 |
| FastAPI / Flask | 没有 HTTP API 的消费者，加一层只是多一个失败点 |
| LangChain / AutoGen | 阶段0 的 ReAct 循环只有 200 行且完全可控；引入框架会掩盖失败模式，而失败模式正是本项目的展示主题 |
| Docker / 微服务 | 单机 CLI，一个进程能跑完 |
| 向量库 / bge-m3 RAG | P2；本阶段"证据"来自工具返回，不来自检索 |

### 环境变量（部署时唯一需要关心的外部状态）

| 变量 | 默认 | 作用 |
|---|---|---|
| `SECAGENT_PROFILE` | `ollama` | 换模型 |
| `SECAGENT_DB` | `secagent/data/secagent.db` | SQLite 文件位置 |
| `OLLAMA_MODELS` | `E:\ollama-models` | 模型仓库（系统已设） |
| `OLLAMA_HOST` / `http_proxy` | — | 阶段0 已确认 `http_probe` 会主动绕开代理 |
| `DEEPSEEK_API_KEY` | 空 | 只在切云端 profile 时需要 |

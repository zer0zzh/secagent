# secagent · 安全智能体（阶段0 完成）

ReAct 循环 + 工具闸门 + 全量 trace。本地 qwen2.5:7b-instruct 驱动，无需密钥、可断网。

## 一分钟跑通

```bash
cd E:/安全智能体/secagent
E:/Ollama/ollama.exe serve                                    # 终端A：模型服务
python -m http.server 8080 --bind 127.0.0.1 --directory lab/www # 终端B：靶场
python -m agent.run "对 http://127.0.0.1:8080 做一次 HTTP 指纹识别并给出结论"
```

或双击 `demo.bat`：自动起靶场、跑验收命令、把 trace 路径打在屏幕上。

实测（21.7 s，3 步收敛）：

```
第 1 步  http_probe  {"url":"http://127.0.0.1:8080"}  → 200 · SimpleHTTP/0.6 Python/3.13.14
第 2 步  port_scan   {"host":"127.0.0.1"}             → 8080、443 open
第 3 步  FINAL                                        → 结论（目标 / 证据 / 判断）
```

## 目录

```
secagent/
├── config/llm.yaml           # 唯一配置入口（5 个 profile，密钥占位）
├── .env                      # 真实密钥（已 gitignore）
├── agent/
│   ├── config.py             # YAML + ${ENV} 展开 + 环境变量覆盖 + 脱敏
│   ├── cli.py                # config show / payload / probe / selftest
│   ├── prompt.py             # ReAct 提示词 + JSON 决策解析（容错）
│   ├── run.py                # 主循环 + 核验闸门 + trace
│   ├── llm/
│   │   ├── base.py           # ChatMessage / ChatRequest / ChatResponse / 异常
│   │   ├── registry.py       # provider → 客户端
│   │   └── providers/
│   │       ├── mock.py           # 本地假服务（链路自检）
│   │       ├── openai_compat.py  # 真调用：ollama / DeepSeek / 任意兼容网关
│   │       └── remote.py         # workbuddy 通道占位（浏览器限定，CLI 不实现）
│   └── tools/registry.py     # 工具白名单 + allowlist + 危险模式拦截 + 截断
├── lab/www/                  # 演示靶场静态页
├── runs/                     # 每次运行的 trace.jsonl（已 gitignore）
├── tools/
│   ├── mock_llm_server.py    # 本地 OpenAI 兼容假服务
│   └── workbuddy_probe.html  # WorkBuddy 免密钥通道网页探针（需 publicConfig）
└── docs/llm-config.md        # 配置字段全表 + 换真实服务的方法
```

## 循环怎么工作

1. 系统提示词给出工具清单 + 严格 JSON 协议（`{"thought","action","args"}` 或 `{"action":"FINAL","answer"}`）。
2. 模型输出 → `parse_decision()` 容错解析（代码块、前后废话、`{"tool":...}` 变体都能吃）。
3. 解析失败 → 回灌错误并重试，连续 3 次才终止；`finish_reason=length` 单独标记为截断。
4. `action != FINAL` → 过闸门执行工具，结果截断到 4000 字回灌给模型。
5. `action == FINAL` → 若工具调用次数 `< --min-tools`（默认 2），**驳回**并告知证据不足，继续循环。
6. 每步写 `trace.jsonl`，含完整 messages、原始回复、耗时、tokens、工具返回。

## trace 长什么样

```
run_start → system_prompt → [llm_request → llm_response → decision → tool_result] × N → final → run_end
```

每行是一个 JSON 事件，`llm_request` 带完整 `messages`（含 system 全文），`llm_response` 带 `raw_text` 与 `latency_ms`。
验收运行：`runs/run-20261105-085204/trace.jsonl`，15 个事件，13.9 KB。

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--max-steps` | 8 | 循环上限 |
| `--min-tools` | 2 | FINAL 前最少工具调用次数（核验闸门） |
| `--profile` | `.env` 的 `SECAGENT_PROFILE` | 换模型：`ollama` / `deepseek` / `mock` |
| `--quiet` | 关 | 只打结论 |

## 工具与安全边界

| 工具 | 能力 | 闸门 |
|---|---|---|
| `http_probe` | GET 单 URL，返回状态码、Server、X-Powered-By、Set-Cookie、标题、正文前 600 字 | 只允许 http/https；host 必须在 allowlist（127.0.0.1 / 私网 / *.local）；绕过系统代理 |
| `port_scan` | TCP 连接探测最多 32 个端口，带回 HTTP 首行横幅 | 同上 |

参数里出现 `;` `|` `&&` `$(` 反引号 `file://` `gopher://` 等一律拒绝执行，拒绝原因回灌给模型。

## 阶段1 工程纪律（先想清楚，再动手，最后验证）

五份文档，按顺序读，改需求先改文档再动代码：

| 阶段 | 文档 |
|---|---|
| 1 需求与范围 | `docs/phase1/01-scope.md`（给谁用 / 解决什么 / 绝对不做什么 / P0~P2 / 5 条用户故事 / 验收线） |
| 2 设计 | `docs/phase1/02-design.md`（8 张表的数据模型 + 技术选型 + 明确不选什么） |
| 3 任务拆解 | `docs/phase1/03-tasks.md`（T0~T9，每任务独立验收命令 + 提交信息 + 依赖图） |
| 4 编码与自测 | `docs/phase1/04-implementation-log.md`（实测输出与提交哈希） |
| 5 部署与复盘 | `docs/phase1/05-deploy-and-retro.md`（环境变量清单 + 阶段0 六个真实卡点） |

Git 已初始化，一个任务一个提交；`.env` / `runs/` / `data/` 均已忽略。

T1 数据层已落地：

```bash
python -m unittest discover -s tests -v          # 7 tests, OK
python -m agent.store init                       # 建 8 张表
python -m agent.store import runs/run-20261105-090111/trace.jsonl
python -m agent.store summary                    # runs=1 steps=3 tool_calls=2
```

## 已知限制（阶段0 有意保留）

- 单线程、无流式；每步重发全量 messages，步数多了 token 线性涨。
- 只有两个工具，够验收不够实战。
- 7B 会把"SSL 证书失败"读成"端口关闭"——已在工具侧返回 `tls_error: true` 纠正，但模型仍可能误判。这正是阶段2 核验层要解决的事。
- `workbuddy` 通道不实现：只对浏览器/小程序开放（Origin 精确匹配 + 强制 SDK），CLI 直连无意义。

## 换模型

```bash
# 本机小模型
SECAGENT_LLM_OLLAMA_MODEL=qwen2.5:3b-instruct python -m agent.run "..."

# 云端（填 key 即可，代码同一份）
# .env: DEEPSEEK_API_KEY=sk-...
SECAGENT_PROFILE=deepseek python -m agent.cli probe
```

配置字段全表见 `docs/llm-config.md`。

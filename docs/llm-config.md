# LLM 配置说明（阶段0）

## 1. 配置放在哪

| 文件 / 变量 | 内容 | 进版本库 |
|---|---|---|
| `secagent/config/llm.yaml` | 全部 provider 档案、模型名、采样参数、超时重试策略 | 是（无真实密钥） |
| `secagent/.env` | 真实密钥（`DEEPSEEK_API_KEY` 等），从 `.env.example` 复制 | **否** |
| `SECAGENT_PROFILE` | 覆盖 `active`，决定这次用哪个档案 | 运行时 |
| `SECAGENT_LLM_<PROFILE>_<FIELD>` | 单字段覆盖，见下表 | 运行时 |
| `secagent/runs/<时间戳>/response.json` | 每次探针的请求/响应/token/耗时留档 | 否（可清理） |

加载优先级（低 → 高）：
`llm.yaml defaults` → `profile.params` → `llm.yaml` 中的 `${ENV}` 展开 → `SECAGENT_LLM_*` 覆盖 → `SECAGENT_PROFILE` 换档案。

## 2. 字段含义

### 顶层

| 字段 | 含义 | 备注 |
|---|---|---|
| `version` | 配置结构版本 | 将来改结构时递增，便于迁移 |
| `active` | 默认生效档案 | 被 `SECAGENT_PROFILE` 覆盖 |
| `defaults` | 所有档案共享的请求参数 | 档案里重名的键会覆盖这里 |

### 档案（profile）

| 字段 | 含义 | 取值 |
|---|---|---|
| `provider` | 客户端类型 | `mock` / `workbuddy` / `openai_compatible` |
| `model` | 模型标识 | 如 `deepseek-chat`、`qwen2.5:7b` |
| `base_url` | API 根地址 | OpenAI 兼容时无需带 `/chat/completions`，代码会拼 |
| `api_key` | 凭据 | 一律写 `${VAR}`，不落明文 |
| `runtime` | 运行环境约束 | `native`（CLI 可直连）/ `browser-sdk` / `miniprogram` |
| `params` | 该档案的请求参数覆盖 | 见下 |
| `description` | 备注 | 只给人看 |

### 请求参数（defaults / params）

| 字段 | 含义 | 建议值 |
|---|---|---|
| `temperature` | 采样温度，越低越确定 | 安全场景 `0~0.3` |
| `top_p` | 核采样，与 temperature 二选一调 | `0.9~0.95` |
| `max_tokens` | 单次回复上限 | ReAct 单步 `≤1024` |
| `stop` | 停止序列 | 一般留空 |
| `stream` | 是否流式 | 阶段0 固定 `false` |
| `timeout_s` | 单次请求超时（秒） | 云端 `60`，本地小模型 `180` |
| `max_retries` | 重试次数（仅 5xx/超时） | `2` |
| `retry_backoff_s` | 退避基数，第 n 次等待 `b^n` 秒 | `1.5` |
| `extra_headers` | 透传请求头 | 如 `X-Trace-Id` |
| `extra_body` | 厂商私有字段，原样并入请求体 | 如 DeepSeek 的 `thinking` |

### 环境变量覆盖表

`SECAGENT_LLM_<PROFILE>_<FIELD>`，`<PROFILE>` 大写，如 `SECAGENT_LLM_DEEPSEEK_MODEL`：

| 后缀 | 对应字段 |
|---|---|
| `_MODEL` / `_BASE_URL` / `_API_KEY` | 档案三要素 |
| `_TEMPERATURE` / `_TOP_P` / `_MAX_TOKENS` | 采样参数 |
| `_TIMEOUT_S` / `_MAX_RETRIES` / `_STREAM` | 运行控制 |

## 3. 阶段0 的验证做到了哪一步

| 验证项 | 手段 | 结论 |
|---|---|---|
| 配置能解析、`${ENV}` 能展开、缺项能提示 | `python -m agent.cli config show` | 通过 |
| 归一化请求体能正确生成 | `python -m agent.cli payload --profile deepseek` | 通过 |
| HTTP 发送 → 响应解析 → 落盘 | `python -m agent.cli selftest`（本地假服务） | 通过 |
| **真模型调用（本机 ollama）** | `python -m agent.cli probe` | **通过，见下表** |
| 结构化输出可直接解析 | 自定义 `--prompt` 要求单行 JSON | 通过（28 tokens 返回合法 JSON） |
| 真实云端鉴权（DeepSeek 等） | — | 待填 `DEEPSEEK_API_KEY`；代码路径已就绪 |
| workbuddy 通道 | — | 不实现（浏览器限定，见下） |

真机实测（qwen2.5:7b-instruct，`OLLAMA_MODELS=E:\ollama-models`，ollama 0.33.3）：

| 场景 | 耗时 | prompt / completion tokens | 结果 |
|---|---|---|---|
| 首次（含加载权重） | 12,692 ms | 36 / 3 | `PONG` |
| 模型常驻后 | 2,885 ms | 64 / 28 | `{"tool":"http_probe","args":{"url":"http://127.0.0.1:8080"}}` |

结论：7B 本地模型足以跑 ReAct 的"单步 JSON 决策"，2.9 s/步在可接受范围（8 步 ≈ 25 s）。


### 为什么没用 WorkBuddy 的免密钥通道做这次验证

WorkBuddy 的 LLM 通道（`/.cloud/llm/chat/completions`）按设计只对**浏览器 / 小程序**开放：

- 初始化必须用 `publicConfig.endpoint` + `publishableKey`，请求携带页面 Origin；
- 服务端按应用发布域名做**精确 Origin 匹配**，不匹配返回 `auth_` 前缀错误；
- 官方明确要求走 SDK（npm / CDN），不允许手搓 `/.cloud/**` 请求。

Python CLI 没有 Origin、也没有对应 SDK，直连必然卡在 `auth_`，且属于官方禁止的用法。
所以它验证不了这条 CLI 链路 —— 拿它当"通过"反而是假阳性。

该通道的正确用法已备好：`tools/workbuddy_probe.html`。将来若要给培训做一个网页版演示（或用它零成本调模型），
开通云服务后把 `endpoint` / `publishableKey` 填进那个页面，点"开始测试"即可。

## 4. 换成真实 LLM

### 云端厂商（DeepSeek / OpenAI / 中转网关）：只填密钥

`openai_compatible` 客户端（`agent/llm/providers/openai_compat.py`）已实现非流式调用、超时、重试、
401/403 鉴权错误映射，所以接入云端不需要写代码：

1. `.env` 填 `DEEPSEEK_API_KEY=sk-...`；
2. `export SECAGENT_PROFILE=deepseek`（或改 `config/llm.yaml` 的 `active`）；
3. `python -m agent.cli probe --profile deepseek`，看到 `状态 ok` 即完成。

### 本机 ollama：已经是可用状态

```bash
E:/Ollama/ollama.exe serve        # OLLAMA_MODELS=E:\ollama-models
python -m agent.cli probe         # 默认 profile=ollama，模型 qwen2.5:7b-instruct
```

换小模型省显存：`SECAGENT_LLM_OLLAMA_MODEL=qwen2.5:3b-instruct python -m agent.cli probe`。

### 阶段1 还需要补的（不是配置问题）

| 缺口 | 位置 |
|---|---|
| 流式输出（SSE 逐帧解析 `delta.content` / `reasoning_content`） | `openai_compat.py` 增加 `chat_stream()` |
| 工具调用的原生 `tools` 参数（qwen2.5 / DeepSeek 均支持） | `ChatRequest` 增加 `tools` 字段 |
| workbuddy 浏览器通道 | 不进 CLI；需要时用 `tools/workbuddy_probe.html` |

### workbuddy 通道为何不实现

`/.cloud/llm/chat/completions` 只对浏览器/小程序开放：必须用 `publicConfig.endpoint` + `publishableKey`
经官方 SDK 初始化，服务端按应用发布域名做精确 Origin 匹配，且官方禁止手搓 `/.cloud/**`。
Python CLI 无 Origin、无 SDK，直连必然卡在 `auth_`，拿它验证 CLI 链路是假阳性。
该通道的正确用法在 `tools/workbuddy_probe.html`（需先开通云服务拿到 publicConfig）。


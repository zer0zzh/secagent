# 阶段1 · 05 部署与复盘

## 一、本地部署（走一遍闭环，倒逼"乱"的根源暴露）

### 部署目标

一台没网、没 Docker、没云端 key 的笔记本上，`demo.bat` 能在 90 秒内跑完一次完整探测并落盘。

### 前置状态清单（部署时唯一需要确认的东西）

| 项 | 检查命令 | 期望 |
|---|---|---|
| 模型服务 | `curl http://127.0.0.1:11434/api/version` | `{"version":"0.33.3"}` |
| 模型仓库 | `dir E:\ollama-models` | 有 `qwen2.5:7b-instruct` |
| 靶场 | `curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/` | `200` |
| 配置 | `python -m agent.cli config show` | `生效 profile ollama` |
| 数据库 | `python -m agent.store init` | 8 张表 |
| Git | `git log --oneline -3` | 每任务一个提交 |

### 环境变量（全部有默认，缺了也能跑）

| 变量 | 默认 | 谁会读 |
|---|---|---|
| `SECAGENT_PROFILE` | `ollama` | `agent/config.py` |
| `SECAGENT_DB` | `secagent/data/secagent.db` | `agent/store.py` |
| `DEEPSEEK_API_KEY` | 空 | 仅切 `deepseek` 时 |
| `OLLAMA_MODELS` | 系统已设 `E:\ollama-models` | ollama 自己 |
| `http_proxy` | 系统已设 `127.0.0.1:37086` | **工具层主动绕开**（见复盘 1） |

### 三步部署

```
1) E:\Ollama\ollama.exe serve
2) 双击 secagent\demo.bat        （自动起 8080 靶场 + 跑验收命令）
3) python -m agent.store import runs\<最新目录>\trace.jsonl
   python -m agent.store summary
```

验收：`python -m unittest discover -s tests -v` 全绿，且 `summary.runs` 增加 1。

### 不许跳过的两条

- **不把 `.env` 拷进任何交付包**（含密钥模板可以，含真实 key 不行）。
- **不在现场第一次跑没跑过的命令**：`demo.bat` 已在 2026-11-05 09:00 实测通过（3 步 / 18 秒）。

## 二、复盘（阶段0 真实卡点，全部有 trace 佐证）

| # | 卡在哪 | 根因 | 怎么修的 | 下一轮纪律 |
|---|---|---|---|---|
| 1 | `http_probe` 对 127.0.0.1:8080 返回 `502 Bad Gateway`，模型得出"服务未启动" | 系统 `http_proxy` 被 urllib 继承，本地请求走了代理；**观测失真**，不是模型笨 | 工具层 `ProxyHandler({})` 显式绕代理；连不上返回结构化 `reachable:false` | 观测层必须与运行环境隔离，任何"看起来像目标响应"的数据都要标注来源 |
| 2 | 模型生成 700 token 被截断，单步 72 s，JSON 残缺 | 输出契约没约束长度，7B 模型自由发挥 | `max_tokens=320`、thought ≤30 字、`finish_reason=length` 单独识别回灌 | 提示词是接口契约，要写长度上限并测截断路径 |
| 3 | TLS 自签名证书失败被读成"端口关闭" | 错误信息里没区分 TCP 失败和 TLS 失败 | 返回 `tls_error:true` + hint "TCP 已连通，端口开放" | 错误分类结构化，不要让模型猜自然语言 |
| 4 | `.bat` 双击报 `'cho' 不是内部或外部命令` | 文件是 LF 行尾；`timeout /t` 被 Git Bash 的 `timeout` 抢走 | 全量转 CRLF，等待改 `ping -n` | 交付物也要当代码测，不能只测 Python |
| 5 | 模型第一次探测就想 FINAL，2 步结束 | 没有"证据足够"的硬门槛 | `--min-tools 2` 核验闸门，证据不足驳回 | 结论必须由闸门放行，不能由模型说了算 |
| 6 | 结论写"未发现其他开放端口"，但工具明明报了 `443 open` | 模型自评不可信 | 工具侧已补结构化字段，**仍未根治** | 阶段2 的 verifier：结论只能由 evidence 生成，模型原文只存档 |

### 最该记住的一条

阶段0 一共返工 4 次，四次都不是"不会写"，而是**没先定好边界就动手**：
没定"什么算有效观测"（1）、没定"输出契约"（2）、没定"错误分类"（3）、没定"结论门槛"（5）。
这正是这套五阶段纪律要防的事——所以阶段1 先落了 01/02/03 三份文档，才写第一行 T1 代码。

### 下一轮立刻调整

1. T2~T9 严格按 `03-tasks.md` 顺序，一任务一提交，验收命令不过不往下走。
2. 每个新工具都先写"返回体长什么样"，再写实现 —— 避免边写边改数据模型。
3. T5 verifier 的输入只认 `evidence.observed_json`，不读 `model_answer`。
4. 每个任务补一条单测，目标是改 A 功能时 B 功能立刻报警。
5. 现场演示前断网跑一遍 `demo.bat`。

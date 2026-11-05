# ReAct 单 Agent 接入真实安全工具（Nmap 7.40）

> 整理时间：2026-11-05 22:40（GMT+8）
> 整理依据：《冲刺清单-安全智能体培训.md》v2 的 **A1 / A3 / A5 / B2 / C1 / C2**
> 上游锚点：DeepSeek 原始分析 R3 行动清单第 1/3/4 条、R4 入局前第 3 条、底层目的「**可靠性大于全能性**」
> 状态：**已在本机跑通**，文中所有数字来自实际运行，非估算

---

## 0. 这份材料是什么

把清单里那句「带一个自己写的小脚本去，讲师讲到工具调用时直接对接」落成一个**能跑、能讲、能拷走**的最小闭环：单 Agent + 一个真实安全工具（Nmap）+ 结果核验。

| 交付物 | 路径（相对 `E:\安全智能体\secagent\`） | 作用 |
|---|---|---|
| 工具适配层 | `lab/nmap_react/nmap_tool.py`（230 行） | 授权闸门、固定命令模板、XML → 结构化证据 |
| ReAct 单 Agent | `lab/nmap_react/react_agent.py`（333 行，核心循环约 70 行） | 决策循环、trace、核验闸门、B2 反例复现 |
| 契约测试 | `tests/test_nmap_react.py`（141 行，17 个用例） | 锁住授权、参数、解析、核验四类契约 |
| 运行痕迹 | `runs/nmap-react-<时间戳>/` | `trace.jsonl` + 原始 `nmap-step1.xml` |
| 本文 | `docs/react-single-agent-nmap.md` | 设计 + 代码讲解 + 实测 + 话术，可直接交给主办方 |

**刻意不改的东西**：阶段 0 已验收的 `agent/run.py`、`agent/tools/registry.py`、`agent/store.py`、`docs/phase1/*` 一行未动；`demo.bat`、GitHub 成果保持原样。本材料是 `lab/` 下新增的教学资产，独立目录、独立运行，不进主链路。

---

## 1. 第一步 · 需求与范围（先想清楚，不打开编辑器）

| 三问 | 答案 |
|---|---|
| **给谁用** | ① 我自己在培训模块 5–8 上机时做演示；② 主讲老师看「工具调用」怎么接；③ 主办方验收「开箱即用资产」 |
| **解决什么问题** | 原分析点名的痛点：**模型自评不能当结论**。让 Agent 调一次真实工具，用证据校验把幻觉结论拦在输出之前 |
| **绝对不做什么** | 公网目标、自动利用链、多智能体、RAG、自建评测体系、量化指标、前端界面、完整漏洞验证系统 |

**核心功能清单（只有 5 条，按优先级）**

| # | 功能 | 为什么必须有 |
|---|---|---|
| P0-1 | 接一个真实安全工具，拿到结构化观测 | A5「核心武器」；不是假数据、不是 mock |
| P0-2 | 单 Agent 跑完「任务下达 → 工具调用 → 反馈调整 → 结果核验」 | R4 明写的四步闭环 |
| P0-3 | 授权边界硬校验，模型绕不过 | C4 / A4 threat model |
| P0-4 | 证据校验闸门：无证据不许出结论 | B2、底层目的「可靠性大于全能性」 |
| P0-5 | 全量 trace + 失败记录 | A3、模块 9 提问素材 |

**三条用户故事**

- 作为**参赛学生**，我希望一条命令就能跑通一次真实工具调用，这样讲师讲工具接入时我不用现搭环境。
- 作为**主讲老师**，我希望工具适配层和 Agent 循环是分开的，这样换成课堂给的工具时只改一个文件。
- 作为**主办方**，我希望看到「结论依据哪条观测」，而不是听模型自己说"我认为"。

---

## 2. 第二步 · 设计蓝图（画蓝图，不砌墙）

### 2.1 闭环结构

```
任务下达（自然语言）
   ↓
[LLM] 单行 JSON 决策 ──────→ nmap_scan ──→ 工具层：授权闸门 → 固定命令 → Nmap 7.40
   ↑                                              ↓
   └──── 观测结果（精简 JSON 回填）←── XML → evidence
   ↓
[LLM] FINAL 结论
   ↓
证据校验闸门（程序判，不经过模型）── 不通过 → 把驳回理由回填，重新给结论
   ↓ 通过
输出结论 + trace.jsonl 落盘
```

**关键设计判断**：核验放在**程序层**，且只读 `evidence.open_ports`，不读模型的自评文本。这是阶段 0 那个真实教训的直接落地——qwen2.5:7b 曾把「443 open」写成「未发现其他开放端口」。

### 2.2 数据契约：`nmap_scan` 返回体

Agent 与核验层只认这张表，工具换成别的安全工具也必须遵守它。

| 字段 | 类型 | 含义 | 谁用 |
|---|---|---|---|
| `ok` | bool | 观测是否成功 | 闸门、Agent |
| `target` / `ports` | str | 实际执行的授权目标与端口 | trace、人工复查 |
| `command` | str | 完整命令行（留痕，不执行第二次） | 人工复查 |
| `nmap_version` | str | `7.40` | 证据可信度 |
| `hosts[].address` / `state` | str | 主机地址、存活状态 | Agent |
| `hosts[].ports[].port` / `state` / `service` / `product` / `version` / `conf` / `cpe[]` | — | 端口级证据 | Agent、闸门 |
| `open_ports` | int[] | **核验层唯一事实来源** | 闸门 |
| `summary` | str | 一行人类可读摘要 | 现场演示 |
| `elapsed_ms` / `stderr` / `error` | — | 执行元信息 | 失败记录 |
| `raw_xml` | str | Nmap 原始输出，单独存 `.xml`，不进 trace | 复查 |

### 2.3 输出契约：ReAct 单行 JSON

```
{"thought":"...","action":"nmap_scan","args":{"target":"127.0.0.1","ports":"8080,11434"}}
{"thought":"...","action":"FINAL","answer":"..."}
```

`thought ≤ 30 字`、`max_tokens=320`、`temperature=0.1`、单行、无 Markdown。这四条是阶段 0 用真实代价换来的：`max_tokens=700` 时模型输出被截断，单步耗时 72 秒；降到 320 后单步 3–10 秒。

### 2.4 授权边界（A4 threat model 落地）

| 危险 tool call 类型 | 这里的拦法 | 代码位置 |
|---|---|---|
| 越权目标（公网 / 他人资产） | `check_target()` 默认只允许 `127.0.0.1` / `localhost` / `::1`；外部靶场必须显式设 `SECAGENT_LAB_CIDR` | `nmap_tool.py:55-77` |
| 参数注入（`-p-`、`;id`、`--script vuln`） | `check_ports()` 正则 + 展开后 ≤64 端口；命令用 list + `shell=False` | `nmap_tool.py:79-110`、`148-165` |
| 任意 flag / 提权 | `FIXED_FLAGS` 写死，模型只能传 `target` 和 `ports` 两个值 | `nmap_tool.py:25` |
| 失控耗时 / 输出膨胀 | `subprocess` 40 s 超时、`--host-timeout 15s`、XML 截断 200 KB | `nmap_tool.py:26-29`、`160-166` |
| 无证据结论 / 幻觉 | `verify()` 三条断言，不通过就回填驳回 | `react_agent.py:118-152` |

> 提示注入本身**不在本次实现范围**：清单 B3 明确把它划到「培训中后期到赛后」。这里只把「观测结果会回到模型上下文」这个观察位置标出来，不冒充做了防御。

### 2.5 技术选型

**只用一套**：Python 3.13 标准库（`subprocess` / `xml.etree` / `urllib` / `unittest`）+ 本机 Ollama `qwen2.5:7b-instruct` + Nmap 7.40。

明确不选：LangChain / AutoGen（把循环藏进框架，现场讲不清）、FastAPI / 前端（多一层部署就多一处崩）、SQLite（本教学件不做持久化，主链路已有）、Docker（培训现场网络不稳）、云端 LLM（断网可演示是硬要求）。

---

## 3. 第三步 · 任务拆解与版本控制

| 任务 | 内容 | 独立验收命令 | 结果 |
|---|---|---|---|
| T1 | 工具适配层：授权闸门 + XML 解析 | `python lab/nmap_react/nmap_tool.py 127.0.0.1 8080,11434` | 通过 |
| T2 | 决策解析 + LLM 直连（绕代理） | `python -m unittest tests.test_nmap_react.TestDecisionParsing -v` | 3/3 |
| T3 | 证据校验闸门 `verify()` | `python -m unittest tests.test_nmap_react.TestVerifier -v` | 8/8 |
| T4 | ReAct 循环 + trace 落盘 | `python lab/nmap_react/react_agent.py "..."` | 2 步收敛 |
| T5 | B2 反例复现 + 本文 | `python lab/nmap_react/react_agent.py --demo-unreliable` | 4/4 拦截 |

每个任务都能单独跑、单独判对错，符合「写完一个接口立刻测一下」。Git 沿用已有仓库，一任务一提交；`.env` / `runs/` / `data/` 仍在忽略列表里。

---

## 4. 第四步 · 编码与自测（关键片段）

### 4.1 工具层：闸门在命令执行之前（`nmap_tool.py:148-165`）

```python
def scan(target: str, ports: str) -> dict[str, Any]:
    host = check_target(target)          # 闸门 1：目标必须先过授权
    port_spec = check_ports(ports)       # 闸门 2：参数必须先过白名单
    cmd = [nmap_exe(), *FIXED_FLAGS, "-p", port_spec, "-oX", "-", host]
    proc = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S,
                          shell=False, check=False)   # 模型无法注入额外 flag
```

授权判定**不经过模型**：即使 prompt 被诱导，工具层也不会把 `8.8.8.8` 传给 Nmap。实测拒绝样例：`8.8.8.8`、`scanme.nmap.org`、`192.168.1.10`、`127.0.0.1; whoami`、`-p-`、`8080;id`、`80,abc`、`1-65535`。

### 4.2 Agent 循环：`run()` 的四步闭环（`react_agent.py:184-280`）

```python
for step in range(1, max_steps + 1):
    resp = llm_chat(messages)                      # 任务下达
    decision, err = parse_decision(resp["text"])   # 决策解析
    if action == "NMAP_SCAN":
        ev = scan(...)                             # 工具调用（真实 Nmap）
        messages.append({"role": "user", "content": observation(ev)})
    elif action == "FINAL":
        ok, reason = verify(answer, evidence)       # 结果核验（程序判）
        if not ok:
            messages.append({"role": "user", "content": f"结论被驳回：{reason}..."})
            continue                               # 反馈调整
        break
```

**反馈调整**不是摆设：解析失败、工具被拒、核验驳回三种情况都会把原因回填进上下文，让模型重写，而不是直接崩掉或硬输出。

### 4.3 核验闸门：只有三条断言（`react_agent.py:118-152`）

| 断言 | 拦什么 | 实例 |
|---|---|---|
| 结论里的端口必须都在 `open_ports` 中 | 编造端口 / 厂商 | `9999 运行 Apache 2.4.49` |
| 有开放端口时不得出现否定表述 | 与观测直接矛盾 | `未发现任何开放端口，主机不可达` |
| 必须引用至少一个证据端口；排他表述不得漏端口 | 无证据结论 / 遗漏观测 | `nginx 1.24` / `8080 是唯一开放端口` |

刻意**只有三条**：它是 B2 要求的「一道人工核验或证据校验步骤」，不是评测体系、不产生误报率一类自创指标，也不宣称解决提示注入。清单红线由此守住。

### 4.4 自测结果

```
$ python -m unittest discover -s tests -v
Ran 24 tests in 0.373s
OK
```

其中新增 17 个（授权 5 / XML 1 / 决策 3 / 核验 8），原有 7 个 `test_store` 全部仍通过 —— **没有改坏阶段 1 的数据层**。

---

## 5. 实测运行记录（2026-11-05 22:37，真机）

**环境**：Nmap 7.40（`D:\Nmap\nmap.exe`）、Ollama 0.33.3、`qwen2.5:7b-instruct`、本地靶场 `http://127.0.0.1:8080`（Python `http.server`）。

```
$ python lab/nmap_react/react_agent.py "对 127.0.0.1 的 8080,11434 端口做服务识别并给出结论"

第 1/5 步 · NMAP_SCAN {'target': '127.0.0.1', 'ports': '8080,11434'}
        → ok=True 8080/http SimpleHTTPServer 0.6; 11434/unknown (7085ms)
第 2/5 步 · FINAL

结论：8080端口开放HTTP服务，版本0.6；11434端口开放未知服务。

步数 2 ｜ 工具调用 1 ｜ trace: runs/nmap-react-20261105-223758/trace.jsonl
real 0m26.714s
```

| 项 | 实测值 |
|---|---|
| 总耗时 | 26.714 s |
| Nmap 真实扫描 | 7,085 ms（含 `-sV --version-light`） |
| LLM 第 1 步 | 14,146 ms（隔夜冷启动，加载权重）/ 233 prompt + 48 completion = 281 tokens |
| LLM 第 2 步 | 4,813 ms / 562 prompt + 45 completion = 607 tokens |
| 收敛 | 2 步、1 次真实工具调用、`status=converged` |
| trace | `trace.jsonl` **12 事件 / 6,002 B** + `nmap-step1.xml` 原始输出 |
| 观测证据 | `8080/tcp open http SimpleHTTPServer 0.6 conf=10`，CPE `cpe:/a:python:simplehttpserver:0.6`；`11434/tcp open unknown conf=3` |

**trace 完整性**（逐事件核对过）：`run_start` → `llm_request`（含完整 messages，system 384 字）→ `llm_response`（`raw_text` + `latency_ms` + `usage`）→ `decision` → `tool_call` → `tool_result`（12 字段完整返回体）→ 第二轮同样齐全 → `verification(passed=True)` → `final` → `run_end(steps=2, tool_calls=1, status=converged)`。原始 XML 单独留档，trace 里只记文件名。

---

## 6. 失败记录（A3）与不可靠案例复现（B2）

### 6.1 失败记录（本机真实卡点，不是假设）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `subprocess` 调 `nmap.exe -sV -p 8080,11434` **60 s 超时**；单端口 8080 却 6.9 s 正常 | `-sV` 默认探测对 11434（Ollama 不回 banner）长时间等待，多端口叠加放大 | 固定加 `--version-light --host-timeout 15s`，两个端口稳定 6.9 s；再加 `subprocess` 40 s 兜底 |
| 2 | 阶段 0 曾因系统代理把 `127.0.0.1` 请求交给代理拿回 502，模型写成「目标不可达」 | `urllib` 默认读 `http_proxy` | 本次 `llm_chat()` 直接用 `ProxyHandler({})`，主动绕开 |
| 3 | 核验初版靠 `reason.startswith("端口与服务")` 判通过，且「Nmap 无开放端口」的正确结论被误杀 | 用字符串代替布尔、边界分支缺失 | 改成 `(ok, reason)` 布尔返回 + 无开放端口单独分支，8 个单测锁住 |
| 4 | `--version-timeout 3s` 参数让 nmap 直接 `rc=-1` 空输出 | 该版本不接受此写法 | 弃用，改用 `--version-light` + `--host-timeout` |

### 6.2 B2 复现记录（模块 9 的真实素材）

`python lab/nmap_react/react_agent.py --demo-unreliable` 先做一次**真实** Nmap 观测，再把四条**构造的反例结论**（明确标注为模拟幻觉，不是评测样本）送入同一道闸门：

```
Nmap 真实观测：ok=True  8080/http SimpleHTTPServer 0.6; 11434/unknown  (6981ms)
开放端口：[8080, 11434]

[1] 拦截  目标 8080 与 9999 端口开放，其中 9999 运行 Apache 2.4.49 → 提到未观测端口 ['9999']
[2] 拦截  扫描完成，未发现任何开放端口，目标主机不可达。        → 与观测 11434,8080 矛盾
[3] 拦截  已完成服务识别，Web 服务为 nginx 1.24，风险等级低。    → 未引用任何证据端口
[4] 拦截  8080 端口是唯一开放的端口，运行 Python SimpleHTTPServer 0.6。 → 排他表述漏掉 11434

拦截 4/4 条；trace: runs/nmap-unreliable-20261105-223751/trace.jsonl
```

第 2 条正是阶段 0 真实幻觉的同类错误（当时模型把 `443 open` 写成「未发现其他开放端口」）；第 4 条是它的变体。现场可以直接说：**「同一条 trace，模型自评会骗人，evidence 不会」**。

---

## 7. 现场 30 秒演示话术（A5 验收物）

> 「我带的是一个最小闭环，**单 Agent、只接一个真实工具 Nmap**，不搞多智能体。
>
> 工具层就三件事：授权白名单、固定命令模板、把 Nmap 的 XML 转成结构化证据——**模型只能传目标和端口两个参数**，传不进别的 flag。
>
> 演示一次：一条命令，2 步收敛，26 秒，trace 里每步的输入输出和原始 XML 都在。
>
> 关键是最后这道闸：结论**只认 evidence，不认模型自评**。我这边准备了四条看着很合理的幻觉结论，现场可以逐条跑，四条全被拦下来，驳回理由直接回填让模型重写。
>
> 这套和课上讲的工具调用是同一个接口契约，老师给的工具我换掉 `nmap_tool.py` 里的 `scan()` 就能接上，Agent 循环一行不用改。」

**与讲师工具接口对接的方法**（模块 5–8 现场用）：适配层契约就三条 —— ① 函数签名 `scan(target, ports) -> dict`；② 返回体必须带 `ok` / `open_ports` / `summary` / `elapsed_ms` / `error`；③ 授权校验在适配层自己做。把 `react_agent.py` 顶部的 `from nmap_tool import ...` 换成新模块即可，核验闸门与循环不用动。**不预设讲师会用什么工具**，避免临场对不上。

**模块 9 提问素材**：亮出 6.2 的复现记录，然后按 R4 原话问 ——「在真实渗透测试中，如果大模型被目标的蜜罐诱导，产生幻觉，咱们这个框架的具体防御机制是什么？」

---

## 8. 第五步 · 部署与复盘 / 开箱即用资产（C2）

### 8.1 运行前置

| 依赖 | 本机值 | 检查命令 |
|---|---|---|
| Nmap | 7.40，`D:\Nmap\nmap.exe` | `nmap --version` |
| Ollama | 0.33.3，`qwen2.5:7b-instruct` | `curl http://127.0.0.1:11434/api/version` |
| 靶场 | `127.0.0.1:8080`（可选，不影响扫描） | `curl --noproxy '*' http://127.0.0.1:8080/` |
| Python | 3.13，仅标准库 | `python -V` |

### 8.2 三步复现（换台机器照做）

```bash
cd /d E:\安全智能体\secagent
python -m unittest discover -s tests -v                      # 24 tests OK
python lab/nmap_react/react_agent.py --demo-unreliable      # 4/4 拦截，无需 LLM
python lab/nmap_react/react_agent.py "对 127.0.0.1 的 8080,11434 端口做服务识别并给出结论"
```

环境变量（都可选）：`SECAGENT_NMAP_EXE` 指定 nmap 路径；`SECAGENT_LAB_CIDR` 显式授权实验网段；`SECAGENT_OLLAMA_MODEL` / `SECAGENT_OLLAMA_BASE_URL` 换模型或后端。

### 8.3 资产清单（可直接交给主办方）

| 类别 | 内容 |
|---|---|
| 代码 | `lab/nmap_react/nmap_tool.py`、`lab/nmap_react/react_agent.py` |
| Prompt 集 | `react_agent.py:35-45` 的 `SYSTEM_PROMPT`、`DECISION_HINT`、驳回回填话术（共 3 处，集中在一个文件） |
| 配置文档 | 本文第 2.2 / 2.3 / 8.1 三节（数据契约、输出契约、环境变量） |
| 测试 | `tests/test_nmap_react.py`，17 个离线用例 |
| 运行证据 | `runs/nmap-react-*/trace.jsonl` 与 `nmap-step*.xml`（`runs/` 不进仓库，现场跑一次即生成） |
| 仓库 | `https://github.com/zer0zzh/secagent` |

### 8.4 复盘（10 分钟）

- **设计先定契约确实省返工**：先写 2.2 数据契约和 2.3 输出契约，再写实现，本次只出现 4 处小修，且没有一次改到主链路。
- **真实工具和假工具差别很大**：Nmap 的 `-sV` 挂死、XML 里的 DOCTYPE、服务字段 `conf=3` 这种不确定性，mock 永远暴露不出来 —— 这正是 A5 要带「真实工具」的价值。
- **最值得讲的不是扫描成功，是被拦下的四条结论**：可靠性大于全能性，和原分析底层目的对齐。
- **下一步不做扩展**：按清单，权限设计深化、输出核验机制、提示注入都留在培训中后期到赛后，不在入局前加码。

---

## 9. 与冲刺清单的对应及边界声明

| 清单条目 | 本材料的对应产出 |
|---|---|
| A1 先修基础自检 | 第 2.5 节技术选型、第 4 节代码讲解（Nmap 读输出写封装、Agent 循环、ReAct、JSON 错误处理） |
| A3 失败记录 | 第 6.1 节四条真实卡点，各附根因与修法 |
| A5 核心武器 + 30 秒话术 | 两个可运行脚本 + 第 7 节话术 + 与讲师工具接口的对接契约 |
| B2 不可靠案例 + 证据校验拦住 | 第 6.2 节 4 条复现记录与 trace |
| C1 结业展示四段 | 第 0/1/5/6 节分别对应痛点、架构、实测数据、展望素材 |
| C2 开箱即用资产 | 第 8.3 节资产清单 |

**边界声明**：本材料**没有**引入清单之外的方向 —— 没有 DVWA/Juice-Shop 双环境、没有自建评测集与误报率指标、没有 payload 验证工具、没有多智能体、没有 90 秒视频或打印稿、没有把提示注入做成防御系统。所有内容都能在 R3 行动清单、R4 入局前 3 件事与冲刺清单 A/B/C 任务里找到出处。

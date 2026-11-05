# Git → GitHub 流程（本机实测版）

> 对象：`E:\安全智能体\secagent` → `https://github.com/zer0zzh/secagent`
> 最后更新：2026-11-05 21:25。**已推送成功**，所有结论都是实测。

## 0. 最终结果

| 项 | 实测值 |
|---|---|
| 远端仓库 | `zer0zzh/secagent`，Public，默认分支 `main` |
| 远端提交数 | **7**（1 个初始化提交 + 复刻的 6 个本地提交） |
| 远端文件数 | **34**，与本地完全一致 |
| 树一致性 | 远端 tree `e558692042…` == 本地 `git rev-parse main^{tree}` `e558692042…`，**字节级一致** |
| 敏感项 | `.env` / `runs/` / `data/` 均不在远端 |
| 认证 | PAT(classic, scope=`repo`)，`api.github.com/user` → 200 |
| 本地 remote | `origin https://github.com/zer0zzh/secagent.git`（干净 URL，不含凭据） |
| 单测 | 7 tests OK |

## 1. 为什么没用 `git push`：两个实测障碍

**① DNS / 网络**

```
nslookup github.com            → 20.205.243.166   ✗ 不通
系统代理 127.0.0.1:12768       → git CONNECT 502
git 直连                       → Empty reply / schannel handshake failed
api.github.com                 → 200（稳定）
```

`E:\安全智能体\github_relay.py` 是本地 CONNECT 中继（127.0.0.1:18888 → 140.82.114.4 / 140.82.112.3），
实测 `ls-remote` 与 smart-http 端点都能通，但**时通时断**，多次 `git push` 卡在 TLS 握手。
不改 hosts、不改系统代理、不改全局 git 配置。

**② 账号密码被禁用**

```
remote: Invalid username or token. Password authentication is not supported for Git operations.
```

`api.github.com/user` 基础认证 401，旧版 `POST /authorizations` 已移除（404）。
**必须 PAT(classic, 勾 `repo`)**。账号口令没有写进任何文件或凭据库（已复查）。

**最终方案**：改用 GitHub Git Data API 复刻提交历史（`api.github.com` 稳定）。

## 2. 复刻是怎么做的

`E:\安全智能体\github_push_api.py`：

1. 空仓库的 Git Data API 会回 `409 Git Repository is empty` → 先用 Contents API
   建一个 `README.md` 初始提交把仓库点亮。
2. 按 `git rev-list --reverse main` 顺序，逐个提交：
   `git ls-tree` 取全部文件 → 上传 blob → 建 tree → 建 commit（parent 映射到远端 SHA）。
3. 最后 `PATCH /git/refs/heads/main` 指向末位提交。
4. 校验：远端 tree SHA 与本地一致。

实测输出：

```
空仓库已初始化，初始提交 ed4aa309
[1/6] fa0da1f5 → 12d33cd0  files=27  blobs_uploaded=26
...
[6/6] 29ff2b4e → e0ace6a3  files=34  blobs_uploaded=39
已更新 refs/heads/main → e0ace6a322
```

### 一个必须知道的后果

**远端提交 SHA 与本地不同**（日期格式经 API 归一化 + 多了初始化父提交）。
内容完全一致（tree SHA 相同），但两段历史是并行的，所以：

- 现在**不要**直接 `git push`，会报 `non-fast-forward`；
- 日常同步用下面第 3 节的 `sync.bat`；
- 网络恢复后可一次性对齐（第 4 节）。

## 3. 后续改动怎么同步（T2~T9 用这个）

```bash
cd /e/安全智能体/secagent
set GITHUB_TOKEN=<PAT>
sync.bat
```

`sync.bat` → `github_push_api.py --sync`：比对本地 `main` 树与远端树，只上传变化的 blob，
以远端 HEAD 为父创建一个新提交，再 `PATCH` ref，最后打印 `tree 一致：True`。
不走 git 传输，因此不受 DNS / 代理波动影响。

当前已验证：`远端与本地已一致（tree e558692042），无需同步`。

本地日常循环不变：

```bash
python -m unittest discover -s tests -v
git add -A && git commit -m "feat(tools): add dir_enum"
set GITHUB_TOKEN=<PAT> && sync.bat
# 把新提交哈希记进 docs/phase1/04-implementation-log.md
```

## 4. 可选：网络恢复后一次性对齐成普通 git 流程

```bash
git -c http.proxy=http://127.0.0.1:18888 fetch origin +refs/heads/main:refs/remotes/origin/main
git rev-parse main^{tree}                 # 与远端 tree 比对，必须相同
git status                                # 必须干净
git reset --hard origin/main              # 内容相同，只换提交 SHA
git branch --set-upstream-to=origin/main main
```

对齐之后 `git push` / `git pull` 就能正常用（前提：github.com 的 git 传输稳定）。
`reset --hard` 前务必确认工作区干净、两次 tree SHA 相同。

## 5. 验证命令

```bash
# 网页应看到 34 个文件、docs/phase1/ 五份文档
curl -s https://api.github.com/repos/zer0zzh/secagent/git/ref/heads/main
curl -s https://api.github.com/repos/zer0zzh/secagent/commits?per_page=100

# 换机复现（需求文档里写死的标准）
git clone https://github.com/zer0zzh/secagent.git secagent-fresh
cd secagent-fresh && cp .env.example .env
python -m unittest discover -s tests -v
python -m agent.store init
```

> clone 出来没有模型、`runs/`、`data/`，正常：它们是运行时产物。

## 6. 报错对照表

| 报错 | 原因 | 处理 |
|---|---|---|
| `Password authentication is not supported for Git operations` | 用了账号密码 | 用 PAT |
| `409 Git Repository is empty` | 空仓库不能直接用 Git Data API | 先用 Contents API 造初始提交（脚本已处理） |
| `CONNECT tunnel failed, response 502` / `Empty reply from server` / `schannel handshake failed` | 本机到 github.com 的 git 传输不稳 | 用 `sync.bat`（走 api.github.com） |
| `! [rejected] non-fast-forward` | 本地与远端是并行历史 | 用 `sync.bat` 或按第 4 节对齐 |
| `src refspec main does not match any` | 本地还叫 `master` | `git branch -M main`（已做） |
| `then: was unexpected at this time` | .bat 的 IF 块里 echo 了未转义括号 | 删括号或写 `^(` `^)`（已修） |
| `LF will be replaced by CRLF` | `core.autocrlf=true` 的正常提示 | 忽略 |

## 7. 边界

- 现场演示走 `demo.bat` + 本地 ollama，**断网也能跑**，不依赖 GitHub。
- GitHub 只承担：版本回溯、换机复现、给主办方看仓库。
- 不推：`.env`（密钥）、`runs/`（trace 原件）、`data/*.db`（运行产物）、`E:\ollama-models`（7.3 G）。
- PAT 用完后可在 GitHub → Developer settings 里撤销；脚本只从环境变量读，不落盘。

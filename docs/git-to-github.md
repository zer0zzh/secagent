# Git → GitHub 流程（本机实测版）

> 对象：`E:\安全智能体\secagent`，本地 Git 仓库 → `https://github.com/zer0zzh/secagent`
> 最后更新：2026-11-05 20:55，所有网络与认证结论都是实测，不是推断。

## 0. 当前状态

| 项 | 实测值 |
|---|---|
| 远端仓库 | `zer0zzh/secagent`，**Public**，空仓库（size=0），默认分支 `main` |
| 本地 remote | `origin https://github.com/zer0zzh/secagent.git`（**干净 URL，不含任何凭据**） |
| 本地分支 | `main`（已对齐远端默认分支） |
| 待推提交 | 以 `git log --oneline` 为准（基线 `fa0da1f` 起，本文相关提交含 `3a32929` 起的文档/脚本提交） |
| 受控文件 | 34 个，`.git` 约 210 KB |
| 单测 | `python -m unittest discover -s tests` → 7 tests OK |
| 密钥扫描 | 无硬编码密钥；`.env` / `runs/` / `data/` 忽略生效 |
| Git 版本 / 凭据助手 | 2.55.0.windows.3 / `store --file C:/Users/root/.my-credentials`（明文，别外传） |
| `gh` CLI / SSH 公钥 | 均未安装/未生成 → 走 HTTPS + PAT |
| `core.autocrlf` | `true`（`.bat` 在本机保持 CRLF；`LF will be replaced by CRLF` 只是提示） |

### 两个实测障碍及处理

**① DNS 给了一个不通的 IP**

```
nslookup github.com            → 20.205.243.166   ✗ 连不上
curl --resolve ...:140.82.114.4 https://github.com        → 200 ✓
curl --resolve ...:140.82.112.3 https://github.com        → 200 ✓
git 走系统代理 127.0.0.1:12768 → "CONNECT tunnel failed, response 502"
git 直连                       → "Empty reply from server"
```

解决：写了 `E:\安全智能体\github_relay.py`，一个本地 CONNECT 中继，把 `github.com:443`
固定连到 `140.82.114.4` / `140.82.112.3`。**不改 hosts、不改系统代理、不改全局 git 配置**。

```bash
python E:/安全智能体/github_relay.py                     # 监听 127.0.0.1:18888
git -c http.proxy=http://127.0.0.1:18888 ls-remote origin
# smart-http 端点实测：200，1.12 s
```

**② 账号密码不能用于 Git 操作**

用提供的账号口令实测推送，GitHub 回：

```
remote: Invalid username or token. Password authentication is not supported for Git operations.
fatal: Authentication failed
```

`api.github.com/user` 基础认证同样 401；旧版 `POST /authorizations` 端点已移除（404）。
**GitHub 自 2021-08 起禁用账号密码做 Git/API 认证，必须 PAT。**
那次尝试用了 `-c credential.helper=`，**没有把口令写进任何凭据文件**（已复查）。

## 1. 唯一还需要你做的一次性动作：生成 PAT

1. 登录 GitHub → 头像 → **Settings** → 左下 **Developer settings**
2. **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)**
3. Note 填 `secagent-push`，Expiration 选 30 天，勾选 **`repo`**
4. **Generate token** → 立刻复制（形如 `ghp_...`，页面关掉就看不到第二次）

> 之前给的口令在本流程里无效，不要反复试——GitHub 会记失败次数。
> 令牌生成后不要写进任何仓库文件；脚本只从环境变量读它。

## 2. 一键推送（本地已全部就绪）

```bash
cd /e/安全智能体/secagent
set GITHUB_TOKEN=<PAT>
push.bat
```

`push.bat` 做的事：检查中继（没跑就拉起）→ 用 `-c credential.helper=` 一次性 URL 推送，
**不把令牌写进 `.git/config` 或凭据文件** → `ls-remote` 验证 → 失败时打印原因。

等价手工命令：

```bash
git -c http.proxy=http://127.0.0.1:18888 -c credential.helper= \
  push "https://zer0zzh:<TOKEN>@github.com/zer0zzh/secagent.git" main:main
```

推送成功后，日常 `git push` 同样要带 `-c http.proxy=http://127.0.0.1:18888`
（本机 DNS 问题期间），或者直接用 `push.bat`。

## 3. 推完验证（30 秒内）

```bash
git -c http.proxy=http://127.0.0.1:18888 ls-remote origin   # 应看到 refs/heads/main
git status                                                  # up to date with origin/main
curl -s https://api.github.com/repos/zer0zzh/secagent
```

网页确认：4 个提交、34 个文件、`docs/phase1/` 五份文档都在，且仓库里**没有** `.env` / `runs/` / `data/`。

换机复现（需求文档里写死的标准）：

```bash
git clone https://github.com/zer0zzh/secagent.git secagent-fresh
cd secagent-fresh
cp .env.example .env
python -m unittest discover -s tests -v       # 7 tests OK
python -m agent.store init                    # 8 张表
```

> clone 出来没有模型、没有 `runs/`、没有 `data/`，正常：它们是运行时产物。

## 4. 日常循环（T2~T9，一任务一提交）

```bash
python -m unittest discover -s tests -v
git add -A
git commit -m "feat(tools): add dir_enum"
set GITHUB_TOKEN=<PAT> && push.bat
# 把新提交哈希记进 docs/phase1/04-implementation-log.md
```

提交格式：`type(scope): 一句话`，type ∈ `feat / fix / test / docs / chore / refactor`。

要试危险改动（比如改 `agent/run.py`）：

```bash
git switch -c exp/verifier
# 随便改、随便提交
git switch main                 # 不满意就回来
git branch -D exp/verifier
```

## 5. 报错对照表（含本机特有问题）

| 报错 | 原因 | 处理 |
|---|---|---|
| `Password authentication is not supported for Git operations` | 用了账号密码 | 生成 PAT，用 `GITHUB_TOKEN` |
| `remote: Invalid username or token` | 令牌错、过期、或私有仓库没勾 `repo` | 重新生成，确认 scope |
| `CONNECT tunnel failed, response 502` | 系统代理（127.0.0.1:12768）不转发 github.com | 用 `push.bat` / 中继 18888 |
| `Empty reply from server` | DNS 解析到 20.205.243.166，该 IP 不通 | 同上，中继固定到 140.82.114.4 |
| `src refspec main does not match any` | 本地还叫 `master` | `git branch -M main`（已做） |
| `! [rejected] ... remote contains work` | 建仓时勾了 README / License | `git pull --rebase origin main`；确认可丢弃才用 `--force-with-lease` |
| `LF will be replaced by CRLF` | `core.autocrlf=true` 的正常提示 | 忽略 |
| `then: was unexpected at this time` | .bat 的 IF 块里 echo 了未转义括号 | 删括号或写 `^(` `^)`（已修） |
| `this exceeds GitHub's file size limit of 100MB` | 误加模型/数据库/trace | 确认 `.gitignore` 有 `runs/ data/ *.db` |
| 误推了密钥 | — | **先去 GitHub 撤销该令牌**，再 `git filter-repo` 清历史；只删文件没用 |

## 6. 可选：SSH / gh CLI

- 无 SSH 公钥；`ssh.github.com:443` 可达。改用 SSH：`ssh-keygen -t ed25519` → 公钥贴 GitHub → `git remote set-url origin git@github.com:zer0zzh/secagent.git` → `ssh -T -p 443 git@ssh.github.com`。
- `gh` 未安装；`winget install --id GitHub.cli` 后可用 `gh auth login` + `gh repo create`。

## 7. 边界

- 现场演示走 `demo.bat` + 本地 ollama，**断网也能跑**，不依赖 GitHub。
- GitHub 只承担：版本回溯、换机复现、给主办方看仓库。
- 不推：`.env`（密钥）、`runs/`（trace 原件）、`data/*.db`（运行产物）、`E:\ollama-models`（7.3 G）。

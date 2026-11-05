# Git → GitHub 流程（本机实测版）

> 对象：`E:\安全智能体\secagent`，本地 Git 仓库已建好，尚未关联远端。
> 本文只整理流程，**没有执行 push**（对外动作需要你的账号与仓库名）。
> 环境数据全部为 2026-11-05 09:33 实测，不是凭印象写的。

## 0. 本机现状（先看清再动手）

| 项 | 实测值 | 影响 |
|---|---|---|
| Git | 2.55.0.windows.3 | 够用 |
| 当前分支 | `master` | **GitHub 默认分支是 `main`，推送前要改名** |
| remote | 无 | 需要 `git remote add origin` |
| 提交数 | 3（`fa0da1f` / `4c42f9e` / `e339bc2`） | 推上去就是这 3 条 |
| 受版本控制文件 | 32 个，`.git` 209 KB（加本文档后 33 个） | 很轻，不会有超时问题 |
| `gh` CLI | 未安装 | 走网页建仓 + HTTPS/PAT，不用 `gh repo create` |
| SSH 公钥 | 无 | 走 HTTPS，不用先配密钥 |
| 凭据助手 | `store --file C:/Users/root/.my-credentials` | 第一次 push 输入一次，之后自动保存（**明文文件，别外传**） |
| `core.autocrlf` | `true` | `.bat` 在本机保持 CRLF；`LF will be replaced by CRLF` 只是提示 |
| 连通性 | `git ls-remote https://github.com/octocat/Hello-World.git` → 成功；`api.github.com` → 200 | 网络没问题 |
| 代理 | 环境变量 `http_proxy=127.0.0.1:37086`，git 自身未设 `http.proxy` | git 会继承环境变量，通常无需额外配置 |
| 密钥扫描 | `git grep` 无硬编码 `sk-` / `ghp_` / 真实 key | 可以安全推 |
| 忽略项 | `.env`、`runs/`、`data/`、`__pycache__/` 均已生效 | trace 与数据库不进仓库 |

## 1. 一次性准备（GitHub 网页上做）

1. 登录 GitHub → 右上角 `+` → **New repository**。
2. 填仓库名（建议 `secagent`），选 Public / Private。
3. **三个初始化选项一个都别勾**：不要 Add README、不要 Add .gitignore、不要选 License。
   本地已有 3 个提交，勾了会产生“远端有本地没有的提交”，第一次 push 直接被拒。
4. 生成访问令牌：头像 → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)**
   - Note：`secagent-dev`
   - Expiration：选 30 天或自定义
   - 勾选 **`repo`**（私有仓库必须；公共仓库至少 `public_repo`）
   - 生成后**立刻复制**，页面关掉就看不到第二次了。下文记作 `<TOKEN>`。

> 2021 年 8 月起 GitHub 不再接受账号密码做 Git 认证，HTTPS 推送的“密码”位置必须填 PAT。

## 2. 本地准备（推之前跑一遍）

```bash
cd /e/安全智能体/secagent

git status                      # 必须干净；有改动先提交
git branch -M main              # master → main，对齐 GitHub 默认分支
git log --oneline -3            # 确认 3 个提交都在
python -m unittest discover -s tests -v    # 7 tests OK，别把没测过的代码推上去
git check-ignore -v .env runs data         # 确认敏感目录仍被忽略
```

## 3. 绑定并推送

```bash
git remote add origin https://github.com/<USER>/<REPO>.git
git remote -v                    # 应显示 fetch/push 两行
git push -u origin main
```

第一次会弹认证：
- **Username**：你的 GitHub 用户名
- **Password**：粘贴 `<TOKEN>`（**不是账号密码**）

成功后凭据写入 `C:/Users/root/.my-credentials`，以后 `git push` 不再问。
`-u` 建立上游跟踪，之后直接 `git push` / `git pull` 即可。

## 4. 验证（推完 30 秒内做完）

```bash
git ls-remote origin            # 能看到 refs/heads/main
git status                      # "Your branch is up to date with 'origin/main'"
```

网页上确认：提交历史、33 个文件、`docs/phase1/` 五份文档都在，且仓库里**没有** `.env`、`runs/`、`data/`。

再验一次“换台机器能不能跑”（这是需求文档里写死的可复现标准）：

```bash
cd /tmp && git clone https://github.com/<USER>/<REPO>.git secagent-fresh
cd secagent-fresh
cp .env.example .env            # 新机器没有 .env
python -m unittest discover -s tests -v        # 7 tests OK
python -m agent.store init                     # 8 张表
```

> clone 出来的仓库没有模型、没有 `runs/`、没有 `data/`，这是正常的：它们是运行时产物，不是源码。

## 5. 日常循环（对应 T2~T9，一任务一提交）

```bash
# 1) 按任务写代码
# 2) 先自测
python -m unittest discover -s tests -v
# 3) 提交
git add -A
git commit -m "feat(tools): add dir_enum"
# 4) 推送
git push
# 5) 把提交哈希记进 docs/phase1/04-implementation-log.md
```

提交信息格式：`type(scope): 一句话`，type 用 `feat / fix / test / docs / chore / refactor`。

**要试危险改动时**（比如改 `agent/run.py` 的循环）：

```bash
git switch -c exp/verifier      # 开临时分支
# ...随便改、随便提交...
git switch main                 # 不满意就回来，main 毫发无损
git branch -D exp/verifier      # 确认不要了再删
```

单人项目不需要 PR；分支只用来隔离实验。

## 6. 报错对照表（按本机环境写）

| 报错 | 原因 | 处理 |
|---|---|---|
| `remote: Repository not found` | 仓库名写错 / 私有仓库 PAT 没勾 `repo` | 核对 URL；重新生成带 `repo` 的 token |
| `Authentication failed for ...` | 填了账号密码，或凭据文件里存着旧 token | 清掉旧凭据再重输：`printf "protocol=https\nhost=github.com\n\n" \| git credential reject`；或直接编辑 `C:/Users/root/.my-credentials` |
| `src refspec main does not match any` | 本地还叫 `master` | `git branch -M main` |
| `! [rejected] ... Updates were rejected because the remote contains work` | 建仓时勾了 README / License | `git pull --rebase origin main` 再 push；确认远端内容可丢弃才用 `--force-with-lease` |
| `Failed to connect to github.com port 443` / 超时 | 代理没生效 | `git config --global http.proxy http://127.0.0.1:37086`；本机已实测可直连，一般不用 |
| `LF will be replaced by CRLF` | `core.autocrlf=true` 的正常提示 | 忽略；`.bat` 在本机仍是 CRLF，双击能跑 |
| `this exceeds GitHub's file size limit of 100MB` | 误把模型 / 数据库 / trace 加进来 | 确认 `.gitignore` 有 `runs/ data/ *.db`；已提交的要先从历史里清 |
| 不小心把 `.env` 或 token 推上去了 | — | **先去 GitHub 撤销/重置那个 token**，再用 `git filter-repo` 清历史；只删文件不解决问题，历史里还在 |

## 7. 可选：换成 SSH（不想管 PAT 时）

本机目前没有 SSH 公钥；22 端口未必通，但 `ssh.github.com:443` 实测可达。

```bash
ssh-keygen -t ed25519 -C "yg@secagent"          # 一路回车
cat ~/.ssh/id_ed25519.pub                       # 贴到 GitHub → Settings → SSH and GPG keys
git remote set-url origin git@github.com:<USER>/<REPO>.git
ssh -T git@github.com                           # 应回 "Hi <USER>! ..."
```

若 22 被网络挡住，在 `~/.ssh/config` 加：

```
Host github.com
  HostName ssh.github.com
  Port 443
  User git
```

## 8. 可选：装 gh CLI 走一条命令

`gh` 本机未安装。装完之后可以跳过网页建仓：

```bash
winget install --id GitHub.cli
gh auth login                       # 选 HTTPS / 浏览器登录
gh repo create secagent --private --source=. --remote=origin --push
```

`api.github.com` 本机实测 200，`gh` 的 API 路径可用。

## 9. 这个项目的边界（别把 GitHub 当部署）

- 现场演示走 `demo.bat` + 本地 ollama，**断网也能跑**，不依赖 GitHub。
- GitHub 只承担：版本回溯、换机复现、给主办方看仓库。
- 不推的东西：`.env`（密钥）、`runs/`（trace 原件）、`data/*.db`（运行产物）、`E:\ollama-models`（7.3 G 模型文件）。

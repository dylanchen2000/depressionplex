# 协作规程：分支 / issue / ignore

> 起因：2026-09-03 道俊要求「涉及到代码和 commit 以及推送的要分支、issue、ignore 搞清楚」。
> 本文是硬规矩，不是建议。改本文需在 `docs/ISSUES.md` 留记录。

## 0. 开工第一条：先 fetch，再判断现状（2026-09-03 补，起因是真事故）

**任何出派工单、写 STATUS、判断"下一步做什么"之前，必须先：**

```bash
git fetch origin && git log --oneline origin/main | head -30 && git show origin/main:docs/STATUS.md
```

起因（DP-023）：2026-09-03 我用 8-24 的**本地快照**当项目现状，出了一份派工单，
把 **DP-020（胶带走廊标定）列为"主线首要任务"——而它 8-24 就已经做完并合入 `main` 了**，
远端还另有 21 个我没有的 commit（rules.py、原语标注工具、单位不变量、盲法约束、
7 视频跨视频泛化全部已完成）。本地 `master` 与远端 `main` 分叉 4 : 22。

**本地仓库不是现状，远端 `main` 才是现状。** 沙箱与 Mac 是两个工作副本，
只有 GitHub 上那份是共同事实。

## 1. 分支

**默认分支是 `main`（不是 `master`）**，`main` **只接受合并，不直接提交**。任何改动先开分支：

| 前缀 | 用途 | 例 |
|---|---|---|
| `feat/` | 新功能 | `feat/human-agreement` |
| `fix/` | 修缺陷 | `fix/panel-band-silent-fallback` |
| `chore/` | 仓库卫生、工具、文档结构 | `chore/repo-hygiene` |
| `spike/` | 探索，**明确允许废弃**，不进 master | `spike/hysteresis-bouts` |

一个分支一个关注点。分支名对应 `docs/ISSUES.md` 里的一条。

## 2. Issue

**GitHub remote 一直可用**（`dylanchen2000/depressionplex`，私有，8-24 建）。
`docs/ISSUES.md` 作为**中文台账**与 GitHub PR 并行使用——台账记决策与理由，PR 记代码评审。

- 每条有稳定编号 `DP-###`，**编号只增不复用**
- commit message 首行必须带编号：`feat(DP-012): 人工评分校验器 + 并集重算`
- 提 PR 时在描述里引 `DP-###`，两边可对上

## 3. Commit

- 首行 `<type>(DP-###): 中文摘要`，≤ 60 字
- 正文写**为什么**，不写做了什么（做了什么看 diff）
- **禁止**把 `data/` 下的原始视频、掩膜、权重塞进 commit（见 §4）
- 每次 commit 前 `python3 run_tests.py` 全绿；不绿就不 commit

## 4. Ignore（踩过坑，别再踩）

`.gitignore` 里有两个反直觉点，都已写在文件注释里：

1. **`data/*` 不能写成 `data/`**。带斜杠的目录规则会让 git 根本不进入该目录，
   后面所有 `!` 例外**静默失效**。实测：写成 `data/` 时 `data/human_scores/*.json` 仍被忽略。
2. **unignore 一个目录后，不要再往里加 `**` 规则**。加了会重新挡住子目录，
   git 不再向下走。实测：`data/human_scores/raw/*.csv` 会被挡掉。

**当前策略**：`data/*` 全忽略，只 `!data/human_scores/` 和 `!data/frozen/` 两个例外；
大文件靠扩展名规则（`*.mp4 *.npy *.pt ...`）兜底，它们在例外目录里同样生效。

**加任何 ignore 规则后必须实测**：

```bash
git check-ignore -q <路径> && echo IGNORED || echo tracked-ok
```

这条不是形式。本项目已有「静默兜底比报错危险」的教训（`panel_band` 两次静默挑错对象），
ignore 规则失效的表现同样是**静默**：文件看着在磁盘上，commit 里没有，
等到要复现验收结果时才发现真值文件从未入库。

## 5. 推送（2026-09-03 全部更正）

**沙箱可以 push。** 之前"无法 push"的记录是错的，已实测：

| 通道 | 状态 | 说明 |
|---|---|---|
| **HTTPS + `~/.git-credentials`** | **可用** | 存有 `ghp_` token，scope 含 `repo`/`delete_repo`/`workflow`，读写皆可。**这是默认通道** |
| GitHub API (`curl` + 同一 token) | 可用 | 建仓、开 PR、改 issue 都能做 |
| `git ls-remote` / `fetch` / `push` HTTPS | 可用 | 已实测 |
| SSH（22 端口） | **不可用** | TCP 通（能拿到 banner）但 `ssh` 客户端握手超时。私钥文件本身可读，8-24 记的"I/O error"已不成立 |
| `gh` CLI | **不可用** | `~/.config` 是文件不是目录 ⇒ `open ~/.config/gh/config.yml: not a directory`。用 `curl` 打 API 代替 |

```bash
git remote add origin https://github.com/dylanchen2000/depressionplex.git
git push -u origin <分支>
```

`git bundle` 保留为**离线备份**手段，不再是主交付通道。

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

### 1.1 叠加 PR（stacked PR）的坑（2026-09-06 实测踩到）

把 PR-B 的 base 设成 PR-A 的分支（为避开同一文件尾部冲突）是可行的，
但**合并 PR-A 时绝对不能带 `--delete-branch`**：base 分支一被删，
**GitHub 会直接把 PR-B 关掉（state=CLOSED），且关闭后无法再改 base**，
只能另开一条新 PR。分支和 commit 不会丢，丢的是 PR 上的讨论与评审记录。

正确顺序：

```bash
gh pr merge <A> --merge                    # 先不删分支
gh pr edit <B> --base main                 # 把 B 的 base 改回 main
gh pr merge <B> --merge --delete-branch    # B 合完再删
git push origin --delete <A的分支>          # 最后单独删 A
```

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

## 6. 收工守卫（2026-09-06 加，起因是真事故）

**收工、交接、换账号前必须跑：**

```bash
bash scripts/closeout_check.sh
```

六节任一非空 = **收工未完成**：未提交改动 / 未跟踪文件 / stash / 无 upstream 的分支 /
领先 origin 的分支 / `tools/` 改动。

起因（DP-050）：2026-09-06 接手时，一台 Mac 上同时存在——两条**从未推送**的分支
（6 和 12 个 commit，里面装着**人工秒表工具 v1→v1.4 的全部源码**，而
DP-006/007/008/010/011/027 六条 open issue 全是在改这个工具）、490 行 trial 级代码
**只以未跟踪文件形式存在**、一个只在本机的 stash、9 个在仓库根目录堆了三天的重复导出。

**共同点不是谁偷懒，是流程里没有任何一步会强制暴露这些状态。** `git push` 不带 stash，
`git status` 不提示分支没有 upstream，工具目录的改动和代码改动混在一起根本看不见。
靠人记得叮嘱是不可靠的——道俊同时负责多个项目会忘，agent 改完工具也会忘。
所以这件事必须机械化：**不是提醒，是一条会返回非零退出码的命令。**

## 7. `gh` CLI（2026-09-06 更正 §5）

§5 记的「`gh` 不可用」**只对沙箱成立**。**Mac 上 `gh` 可用且已登录**
（`/opt/homebrew/bin/gh`，account `dylanchen2000`，凭证在 keyring）。
通过 Bridge 在 Mac 上开 PR：

```bash
gh pr create --repo dylanchen2000/depressionplex --base main \
  --head <分支> --title "..." --body-file <文件>
```

补充实测：沙箱侧**没有** `~/.git-credentials`（那是 Account A 沙箱才有的），
Mac 侧也没有该文件（凭证在 macOS keychain，所以 `git push` 能成但 `grep` 拿不到 token）。
⇒ **PR 一律在 Mac 侧用 `gh` 开，token 不出本机。**

## 8. 派工规范（2026-09-06 加，道俊定的分工模型）

**起因**：2026-08~09 那段乱象的根因是**三个工具的 agent 同时在改同一批东西**
（两条分支各自演进、两套标注契约互相否决、同一 trial 两个计分窗口）。
分工模型因此改为：**架构与判断集中，执行才外派。**

| 角色 | 干什么 |
|---|---|
| **Capy（架构师）** | 出 issue、定分支与 PR 边界、写规范、做需要判断的活（真值口径、验收门、失败归因）。**默认自己干** |
| **外派** | 只在活是机械的、边界清楚的、可机器验收时才派；优先在 Capy 内用便宜模型（如 Sonnet 5） |
| **一律不派** | 真值口径 / 验收门 / 契约裁决 / 失败归因。这四类正是乱象的来源 |

**派工单必须写全七段，缺一段不许发：**

1. 目标一句话 + 对应 `DP-###`
2. 哪台机器、哪个目录、**哪条分支**（分支名派工方给死，不许执行者自己起）
3. 输入文件的**绝对路径，且存在性由派工方先确认** —— 不许让执行者「自己找」。
   找不到就猜、或者重新造一批代替，是 DP-042 已经吃过的教训
4. 步骤，每步都要有**可机器判定**的产出
5. **不许做什么**（例：不许重切视频代替验证映射；不许改验收门常量；不许提交 `data/` 下大文件）
6. **验收命令**（`python3 run_tests.py` 必须全绿）
7. **交付即落地**——下面这段照抄进派工单，不许省：

```bash
python3 run_tests.py                      # 必须全绿，不绿就不 commit
git add <明确列出的路径>                    # 不许 git add -A
git commit -m "<type>(DP-###): 中文摘要"    # 首行 ≤60 字，正文写为什么
git push -u origin <分支名>
gh pr create --repo dylanchen2000/depressionplex --base main \
  --head <分支名> --title "..." --body-file <文件>
bash scripts/closeout_check.sh             # 必须 exit 0
```

**回报必须给出四项：commit sha、分支名、PR 链接、测试通过/失败数。缺任一项视为未交付。**

理由与 §6 同源：**道俊同时负责多个项目，会忘记叮嘱 commit 与合并；agent 改完工具也会忘。
所以"记得推"不能写在提醒里，必须写在派工单的验收条件里。**

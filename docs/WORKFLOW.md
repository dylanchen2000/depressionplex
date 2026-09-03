# 协作规程：分支 / issue / ignore

> 起因：2026-09-03 道俊要求「涉及到代码和 commit 以及推送的要分支、issue、ignore 搞清楚」。
> 本文是硬规矩，不是建议。改本文需在 `docs/ISSUES.md` 留记录。

## 1. 分支

`master` **只接受合并，不直接提交**。任何改动先开分支：

| 前缀 | 用途 | 例 |
|---|---|---|
| `feat/` | 新功能 | `feat/human-agreement` |
| `fix/` | 修缺陷 | `fix/panel-band-silent-fallback` |
| `chore/` | 仓库卫生、工具、文档结构 | `chore/repo-hygiene` |
| `spike/` | 探索，**明确允许废弃**，不进 master | `spike/hysteresis-bouts` |

一个分支一个关注点。分支名对应 `docs/ISSUES.md` 里的一条。

## 2. Issue

**仓库还没有 GitHub remote**（沙箱无 SSH key、无 `gh`，见 `docs/STATUS.md`），
所以 issue 暂时用**仓库内文件** `docs/ISSUES.md` 管理，一条一行表格。

- 每条有稳定编号 `DP-###`，**编号只增不复用**
- commit message 首行必须带编号：`feat(DP-012): 人工评分校验器 + 并集重算`
- 仓库上 GitHub 后，`DP-###` 一次性迁成 GitHub issue，编号沿用，本文件改为只留映射表

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

## 5. 推送

沙箱**无法 push**（无 SSH 私钥、`~/.config` 损坏故 `gh` 不可用）。
交付方式：`git bundle create outputs/depressionplex-<n>.bundle --all`，含完整历史。
道俊在 Mac 上 `git clone`/`git fetch` 该 bundle 后再推 GitHub 私有仓库。

**不要在沙箱里反复试 push / 试 gh，已确认无解。**

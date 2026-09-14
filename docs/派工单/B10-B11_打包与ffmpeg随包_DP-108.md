# 派工单 B10 + B11：Windows 打包（双 spec + Inno Setup）与 ffmpeg 随包（DP-108）

**先读 `docs/派工单/_共同约定_v1.md`，本单只写它没覆盖的部分。**

架构：`docs/SPEC_产品化总体架构_v1.md` §3.4（进程边界、双 spec、三重封堵）、§6.1 的 B10/B11 行。
里程碑：**M2 研究版 = B6 + B7 + B8 + B10 + B11**，道俊 2026-09-13 已批「可以发」。
本单是 M2 里唯一「客户机上装不上就全盘作废」的一环，所以判据全部落在**装完能跑**上。

B10 与 B11 合成一单，因为它们其实是同一件事：**冻结之后，进程还找不找得到它要的那个可执行文件**。

---

## 0. 架构师先裁的三件事（不许自行改动，改了要退回）

### 0.1 后端 exe 的名字与位置：**`DEPRESSION-PLEX/backend/depression-analyzer.exe`**

**现在仓里有两份互相矛盾的说法**，这是本单第一件要修的事：

| 出处 | 说的是 |
|---|---|
| 架构 §3.4 | 后端叫 `depression-analyzer`，**整个文件夹**装进 `DEPRESSION-PLEX/backend/` |
| `desktop/app/services/engine.py::engine_command()`（B3 交付） | 冻结后找**与 GUI 同目录**的 `dp-engine.exe` |

**裁决：按架构走**（`backend/depression-analyzer.exe`），因此**要改的是 `engine.py`**，不是架构。
理由两条：① 后端用 one-folder 冻结，一个 exe 旁边跟着几十个 `.pyd`/`.dll`，摊在 GUI 同级目录里
用户一眼看到的是一堆垃圾，装/卸/杀毒白名单都不好做；② 后端**每段录像起一次进程**，
one-file 每次启动都要把 numpy 解到临时目录（秒级开销 + 临时文件堆积），one-folder 没有这个代价。

**改法必须是「一个名字只有一个来源」**：在 `engine.py` 顶部加

```python
ENGINE_SUBDIR = "backend"
ENGINE_STEM = "depression-analyzer"
```

`engine_command()` 用它们拼路径；**spec 里的 `name=` 必须与 `ENGINE_STEM` 相同，且由测试断言**
（见 §3 守卫 1）。这条守卫是本单价值最高的一条：DP-102 那四个缺陷的根因就是
「同一个名字有两份派生器，而两边的测试各喂自己的夹具」。

### 0.2 ffmpeg 的解析顺序：**env → 随包 → PATH**，且只许有一个解析器

放在**后端旁边**：`DEPRESSION-PLEX/backend/ffmpeg/{ffmpeg.exe,ffprobe.exe}`。
GUI 永远不解码（§3.4），所以它不需要看见 ffmpeg。

在 `depressionplex/video.py` 里加**唯一**的解析函数（替掉现在的 `_require`）：

1. 环境变量 `DPX_FFMPEG` / `DPX_FFPROBE`（**最高优先**，给 CI 与现场排障用，同 `DPX_ENGINE_CMD` 的路子）
2. **随包**：冻结时 `Path(sys.executable).parent / "ffmpeg" / 名字`；源码跑时仓根 `vendor/ffmpeg/名字`
3. 系统 `PATH`（`shutil.which`）
4. 都没有 ⇒ 抛 `VideoError`，**把找过的三处路径全列出来**。
   现在那句提示写的是 `brew install ffmpeg`（Mac 装法），产品装在 Windows 上，**要改**。

**为什么随包排在 PATH 前面**：客户机上那个 ffmpeg 是哪年哪个 build 我们不知道，
而解码器版本会改变解出来的帧（DP-071 那条「转码 vs 源片」的教训就在这一层）。
**随包的那一份才是我们验过的**，系统里的只能当兜底。

### 0.3 版本号与 ffmpeg 二进制

- **版本号唯一来源是 `pyproject.toml`**（`cli/analyze.py::_get_tool_version()` 已经这么读）。
  安装包名 `DEPRESSION-PLEX-Setup-x.y.z.exe` 与 Inno Setup 的 `AppVersion` **必须在构建时从它取**，
  **不许在 `.iss` 里手写版本号**（手写的那天起，报告里的 `tool_version` 和安装包版本就会各走各的）。
- **ffmpeg 二进制不许进 git**（本项目硬规矩：大文件不进仓）。CI 里下载，**SHA256 必须钉死在仓里**，
  校验不过就让构建失败。
- **必须用 LGPL 版 ffmpeg**（例如 BtbN 的 `*-lgpl-shared` 或 gyan.dev 的 LGPL 构建），
  并把它的 `LICENSE`/`COPYING` 一起装进 `backend/ffmpeg/`。
  **不许图省事拿 GPL 版**——我们发的是闭源商业软件，GPL 版会把整个产品拖进 GPL。
  这条不是技术偏好，是法律边界；拿不准就在交付报告里问，**不许自己选**。

---

## 1. 要做什么

新建 `packaging/` 目录：

| 文件 | 内容 |
|---|---|
| `packaging/build_windows.spec` | GUI（`desktop/main.py`）one-folder。**必须 `excludes=["depressionplex"]`**（三重封堵第二重）；`pathex=[仓根]`；`hiddenimports` 补 `desktop.app.*`；`name="DEPRESSION-PLEX"` |
| `packaging/build_analyzer_windows.spec` | 后端（`depressionplex/cli/analyze.py`）one-folder，`name=ENGINE_STEM`；**不许 import PySide6**（`excludes=["PySide6"]`），装出来体积才不会翻倍 |
| `packaging/installer.iss` | Inno Setup：把 GUI 文件夹装到 `{autopf}\DEPRESSION-PLEX\`，后端整个文件夹进 `backend\`，ffmpeg 进 `backend\ffmpeg\`；开始菜单快捷方式；卸载干净。版本号从命令行 `/DAppVersion=` 传入 |
| `packaging/fetch_ffmpeg.py` | 下载 + **SHA256 校验** + 解出 `ffmpeg.exe`/`ffprobe.exe`/许可文件到 `vendor/ffmpeg/`。校验不过 ⇒ 非零退出。哈希与下载地址写在文件里（**只此一处**） |
| `packaging/README.md` | 怎么在本机复现一次构建；产物结构树；已知限制 |
| `.github/workflows/build-windows.yml` | `on: workflow_dispatch` + `on: push: tags: ['v*']`。步骤：checkout → setup-python 3.11 → pip install PySide6==6.7.3 pyinstaller → `python packaging/fetch_ffmpeg.py` → 两次 PyInstaller → Inno Setup（`runner.os == 'Windows'`，用 `iscc`）→ upload-artifact |

改动（都要动，别怕）：

- `desktop/app/services/engine.py`：§0.1 的两个常量 + `engine_command()` 走 `backend/`。
- `depressionplex/video.py`：§0.2 的唯一解析函数；`FFPROBE`/`FFMPEG` 两个裸字符串常量
  **不许再被别处直接当命令用**。
- `depressionplex/cli/analyze.py` 的 `_build_run_json()`：加一个 `"decoder"` 块 ——
  `{"ffmpeg_path": ..., "ffprobe_path": ..., "source": "env"|"bundled"|"path", "ffmpeg_version": ...}`。
  **审计包必须能回答「这批帧是哪个解码器解出来的」**（B6 要印它）。
  版本取 `ffmpeg -version` 第一行；取不到写 `null` 并往 **stderr** 写一行原因，
  **不许写空串冒充**（照 `_get_tool_version()` 的先例，stdout 要逐位稳定，不许碰）。
- `.github/workflows/tests.yml`：不动。

---

## 2. 沙箱验不了什么（先说清，免得你在这上面耗）

沙箱里没有 Windows、没有 PyInstaller、没有 Inno Setup，**也没有能用的 PySide6**。
所以本单的本地验证只有两类：**静态解析 spec/iss 文本**，与 **CI 上真跑一次构建**。
不要试图在沙箱里 `pip install pyinstaller` 然后跑。

---

## 3. 测试（新建 `tests/test_packaging_contract.py`，登记进 `run_tests.py` 的 `TEST_MODULES`）

全部只用标准库（`ast` / 正则 / 读文本），沙箱里必须能跑。每条都要能被变异打红。

1. **exe 名字只有一个来源**：`ast` 解析 `build_analyzer_windows.spec`，取出 `name=` 的字符串常量，
   断言等于 `engine.ENGINE_STEM`；同理 GUI spec 的 `name` 等于 `"DEPRESSION-PLEX"`。
   （**本单最重要的一条**：这两个名字对不上，装出来的软件一按「开始分析」就报「找不到引擎」。）
2. **GUI spec 必须 `excludes` 掉引擎包**：断言 `"depressionplex" in excludes`（AST 取列表元素，不许 grep 文本）。
3. **后端 spec 必须 `excludes` 掉 PySide6**。
4. **`.iss` 里不许出现硬写的版本号**：正则扫 `\d+\.\d+\.\d+`，出现即红；必须有 `AppVersion={#AppVersion}` 这类占位。
5. **`.iss` 的目标路径与 §0.1 一致**：必须出现 `backend\` 与 `backend\ffmpeg\`，且 exe 名与守卫 1 同源（从 `engine.ENGINE_STEM` 拼出来去 `in` 它）。
6. **ffmpeg 解析顺序**：`monkeypatch` 式地把 `shutil.which` 与 `sys.executable`/`sys.frozen` 换掉
   （本仓没有 pytest，直接改模块属性再改回来，照 `tests/` 里现成的做法），
   断言四种情形各走对分支：只有 env、只有随包、只有 PATH、三者都在（**必须选 env**）；
   随包与 PATH 都在 ⇒ **必须选随包**；都没有 ⇒ `VideoError` 且报错里三处路径都在。
7. **只有一个解析器**：AST 守卫 —— `depressionplex/**/*.py` 里除了那个解析函数，
   不许再有别处把 `"ffmpeg"` / `"ffprobe"` 字面量直接放进 `subprocess` 的 argv。
8. **`fetch_ffmpeg.py` 的哈希不许是占位**：断言它里面的 SHA256 是 64 位十六进制，
   且**校验失败会非零退出**（喂一段假数据给它的校验函数）。
9. **run.json 的 `decoder` 块**：拿引擎真跑一次（照 `tests/test_analyze_cli.py` 的合成路子），
   断言 `decoder` 四个键都在；`ffmpeg_version` 取不到时是 `None` 而**不是** `""`。
10. **许可文件必须进包**：`.iss` 与 `fetch_ffmpeg.py` 里都要出现 ffmpeg 许可文件名，缺了即红。

---

## 4. 怎么验收（缺一条即退回）

1. `python3 run_tests.py` 全绿（基线 406 + 你新增的条数；跑一次约 4–5 分钟，用后台跑）。
2. **变异检验自己做**：写 `/tmp/mutate_b10.py`，每条守卫至少一个变异（改掉 spec 里的 name、
   删掉 `excludes`、在 `.iss` 里手写版本号、把 PATH 排到随包前面、把哈希改成占位…），
   逐条报「吃劲/装饰」，装饰的改到吃劲。交付报告里贴这张表。
3. `Tests` + `Desktop Self-Test` 双绿（后者不受本单影响，但不许被你弄红）。
4. **`build-windows` 工作流真跑一次**（`workflow_dispatch`），产物里要有
   `DEPRESSION-PLEX-Setup-x.y.z.exe`，且工作流日志里能看到：ffmpeg 哈希校验通过、
   两次 PyInstaller 成功、Inno Setup 成功。**把 run id 与产物名写进交付报告。**
5. 交付报告额外回答：
   - 装出来的目录树长什么样（贴出来，到第二层）；
   - **没验到什么**：客户机上「双击可装可跑」这条只有真 Windows 机器能验，
     你要明确写「未验证」，不许写成「应该可以」（本项目铁律：查不到 ≠ 没有，验不了就说验不了）；
   - 安装包**没有代码签名**这件事的后果（SmartScreen 会拦一下）——写进 `packaging/README.md` 的已知限制。

## 5. 本单特有的禁令

- 不许把 ffmpeg 二进制、`.exe`、`.zip` 加进 git。
- 不许用 GPL 版 ffmpeg（§0.3）。
- 不许在 `.iss` 或工作流里手写版本号。
- 不许让 GUI 的 spec 打进 `depressionplex` 包（三重封堵第二重就是它）。
- 不许改任何判定参数、阈值、CSV 字段（本单一个科学数字都不该动）。
- 不许把带 token 的 remote URL 写进任何文件、日志、commit message 或 PR 正文。

## 6. 工作区隔离 + 交付

- 从 `main` 最新 head 开分支 `feat/dp108-packaging`，工作区用一个新的 git worktree
  （`git worktree add ../wt-b10 -b feat/dp108-packaging main`），**不要碰同级其他 wt-\* 目录**。
- 提 PR，正文写清：新增测试几条、变异几吃劲几装饰、`build-windows` 的 run id 与产物名、
  以及「客户机双击可装可跑」这条**尚未验证**。

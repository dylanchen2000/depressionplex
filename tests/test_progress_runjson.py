"""测试进度回调与 run.json（DP-099 / B3a）。

验收判据（五条，每条都要能独立失败）：
1. 同一批合成帧，progress=None 与 progress=<收集器> 跑出的报告逐位相同
2. 收集器收到的最后一次调用的第一个参数 == 帧数
3. 回调抛异常时异常会往上抛（证明没被吞）
4. run.json 的键与 CSV_FIELDS 的交集只有 assay / fps
5. 存在未产出数字的隔间时，run.json 的 not_scored 里有它和原因原文

复核（架构师）时补的守卫，每条都做过变异测试（改坏对应代码，本文件必须红）：

6. 进了 CSV 的隔间不许出现在 `chamber_validity` 里（判据 4 只比最外层键名，
   而 CSV 叫 `validity_status`、run.json 叫 `chamber_validity[].status`，
   键名不同但同源不同路 ⇒ 按隔间号比才拦得住）
7. `_build_run_json` 的 `scored` 不许有默认值（有默认值 = 漏传时安静退回旧行为）
8. 总帧数未知时传 None 而不是 0；知道总数的那层负责补上
9. `bl_est=0.0` / `note=None` 不许被序列化成 null / ""（未知不许长成数字的样子）
10. 版本号只认 pyproject 的 `[project]` 表，且在本仓里不许退化成 "unknown"
11. run.json 必须带实际生效的 FROZEN 判定参数（§3.5 点名 θ_mob，原交付漏了），
    且现读 dataclass、字段集与 dataclass 一致、分析入口没有参数覆盖口
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from unittest import mock

import numpy as np

from depressionplex import runner as R, video
from depressionplex.cli import analyze

# 复用 test_runner.py 的合成帧生成器
H = 268
W_CH = 95
W_PILLAR = 4
N_CH = 4
FPS = 10.0

MOVE, STILL, EMPTY = "move", "still", "empty"


def _chamber(kind: str, i: int) -> np.ndarray:
    g = np.full((H, W_CH), 250.0)
    g[0:70, :] = 10.0
    g[H - 9:, :] = 10.0
    g[70:150, 44:50] = 15.0
    if kind == EMPTY:
        return g
    if kind == MOVE:
        top = 150 if i % 2 == 0 else 158
        col = 36 + 3 * (i % 6)
        g[top:190, col:col + 12] = 20.0
    else:
        g[152:182, 18:30] = 20.0
    return g


def _frame(kinds: tuple[str, ...], i: int) -> np.ndarray:
    w = N_CH * W_CH + (N_CH + 1) * W_PILLAR
    g = np.full((H, w), 10.0)
    for k, kind in enumerate(kinds):
        c0 = W_PILLAR + k * (W_CH + W_PILLAR)
        g[:, c0:c0 + W_CH] = _chamber(kind, i)
    return g


def _frames(kinds: tuple[str, ...], n: int) -> list[np.ndarray]:
    return [_frame(kinds, i) for i in range(n)]


# ---- 判据 1：progress=None 与有回调时报告逐位相同 -------------------------


def test_progress_does_not_change_results() -> None:
    """同一批帧，带/不带 progress 跑出的报告必须逐位相同。"""
    kinds = (MOVE, STILL, MOVE, STILL)
    calib = _frames(kinds, 8)
    frames = _frames(kinds, 20)

    # 不带 progress
    plan1, reports1, skipped1 = R.analyze_frames(
        calib, frames, fps=FPS, assay="TST", trial_prefix="test1", n_chambers=4)

    # 带 progress（收集调用）
    calls = []

    def collect(current: int, total: int) -> None:
        calls.append((current, total))

    plan2, reports2, skipped2 = R.analyze_frames(
        calib, frames, fps=FPS, assay="TST", trial_prefix="test2", n_chambers=4,
        progress=collect)

    # 计划必须相同
    assert len(plan1.chambers) == len(plan2.chambers)
    assert len(plan1.warnings) == len(plan2.warnings)

    # 报告数字必须相同（逐隔间比对）
    assert set(reports1.keys()) == set(reports2.keys())
    for k in reports1:
        r1, r2 = reports1[k], reports2[k]
        assert r1.immobility_mirror_pipeline_s == r2.immobility_mirror_pipeline_s
        assert r1.scorable_frames_window == r2.scorable_frames_window
        assert r1.validity_status == r2.validity_status

    # skipped 必须相同
    assert skipped1 == skipped2

    # 确保回调确实被调用了
    assert len(calls) > 0, "进度回调一次都没被调用"


# ---- 判据 2：最后一次调用的 current == 帧数 --------------------------------


def test_progress_last_call_reports_total_frames() -> None:
    """收集器收到的最后一次调用的第一个参数（已处理帧数）== 帧数。"""
    kinds = (MOVE, STILL, MOVE, STILL)
    calib = _frames(kinds, 8)
    n_frames = 15
    frames = _frames(kinds, n_frames)

    calls = []

    def collect(current: int, total: int) -> None:
        calls.append((current, total))

    R.analyze_frames(
        calib, frames, fps=FPS, assay="TST", trial_prefix="test",
        n_chambers=4, progress=collect)

    assert len(calls) > 0, "进度回调一次都没被调用"
    last_current, _ = calls[-1]
    assert last_current == n_frames, \
        f"最后一次调用报告 current={last_current}，期望 {n_frames}"


# ---- 判据 3：回调抛异常时异常会往上抛 --------------------------------------


def test_progress_exception_propagates() -> None:
    """回调抛异常时，异常会往上抛（不会被 try/except: pass 吞掉）。"""
    kinds = (MOVE, STILL, MOVE, STILL)
    calib = _frames(kinds, 8)
    frames = _frames(kinds, 10)

    class TestException(RuntimeError):
        pass

    def explode(current: int, total: int) -> None:
        if current >= 3:
            raise TestException("故意抛出的测试异常")

    try:
        R.analyze_frames(
            calib, frames, fps=FPS, assay="TST", trial_prefix="test",
            n_chambers=4, progress=explode)
    except TestException:
        pass  # 符合预期
    else:
        raise AssertionError("回调抛出的异常被静默吞掉了")


# ---- 判据 4：run.json 键与 CSV_FIELDS 交集只有 assay / fps ----------------


def test_runjson_no_csv_overlap() -> None:
    """run.json 的键（递归展开）与 CSV_FIELDS 的交集只允许 assay / fps。

    这条硬约束防止"一个数字两个序列化器 = 迟早漂移"（DP-054）。
    """
    with mock.patch.dict("os.environ", {"DPX_FFMPEG": "/fake/ffmpeg", "DPX_FFPROBE": "/fake/ffprobe"}), \
         mock.patch("pathlib.Path.exists", return_value=True):
        # 构造一个最小的 run.json 样本
        from depressionplex import video
        from depressionplex.assay_core import segment, validity

        # 构造假的 info/plan
        info = video.VideoInfo(
            path=Path("/fake.mp4"), fps=25.0, n_frames=100,
            width=400, height=268, frame_count_source="nb_frames")

        # 构造假的 plan（单隔间，走廊失败 → 会进 skipped）
        ch = R.ChamberPlan(
            index=1, col_range=(0, 95), corridor=None, suspension=None,
            source="test")
        cv = validity.ChamberValidity(
            chamber=1, status="valid", max_area=100.0, ref_body_area=90.0,
            body_threshold=45.0, ever_had_body=True, note="", occupied_fraction=1.0,
            unsegmentable_fraction=0.0)
        tv = validity.TrialValidity(chambers=(cv,))
        plan = R.TrialPlan(
            chambers=(ch,), trial_validity=tv, calib_indices=(0, 10, 20),
            warnings=("test_warning",))

        skipped = {1: "悬挂点不可估（走廊标定失败 ⇒ 悬挂点不可估（不猜））⇒ 拒绝产出数字"}

        ffmpeg_path, ffmpeg_source = video._resolve_ffmpeg_tool("ffmpeg")
        ffprobe_path, ffprobe_source = video._resolve_ffmpeg_tool("ffprobe")
        run_data = analyze._build_run_json(info, plan, "TST", skipped, set(),
                                           ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source)

        # 递归提取所有键名
        def extract_keys(obj, prefix=""):
            keys = set()
            if isinstance(obj, dict):
                for k, v in obj.items():
                    full_key = f"{prefix}.{k}" if prefix else k
                    keys.add(full_key)
                    keys.update(extract_keys(v, full_key))
            elif isinstance(obj, list):
                for item in obj:
                    keys.update(extract_keys(item, prefix))
            return keys

        run_keys = extract_keys(run_data)
        csv_fields = set(analyze.CSV_FIELDS)

        # 找出最外层键与 CSV_FIELDS 的交集
        top_level_keys = {k.split(".")[0] for k in run_keys}
        overlap = top_level_keys & csv_fields

        # 只允许 assay 和 fps（这两个是标识不是指标）
        allowed = {"assay", "fps"}
        forbidden = overlap - allowed

        assert not forbidden, \
            f"run.json 出现了 CSV 里已有的键：{forbidden}。" \
            "一个数字两个序列化器 = 迟早漂移（DP-054）"


# ---- 判据 5：not_scored 包含未产出数字的隔间 -------------------------------


def test_runjson_not_scored_present() -> None:
    """存在未产出数字的隔间时，run.json 的 not_scored 里有它和原因原文。"""
    with mock.patch.dict("os.environ", {"DPX_FFMPEG": "/fake/ffmpeg", "DPX_FFPROBE": "/fake/ffprobe"}), \
         mock.patch("pathlib.Path.exists", return_value=True):
        # 直接构造一个有 skipped 的场景（模拟走廊标定失败）
        from depressionplex import video
        from depressionplex.assay_core import validity

        # 构造假的 info
        info = video.VideoInfo(
            path=Path("/fake.mp4"), fps=FPS, n_frames=100,
            width=400, height=H, frame_count_source="nb_frames")

        # 构造一个走廊为 None 的隔间（会导致悬挂点为 None → 跳过）
        ch1 = R.ChamberPlan(
            index=1, col_range=(0, 95), corridor=None, suspension=None,
            source="test")
        ch2 = R.ChamberPlan(
            index=2, col_range=(100, 195),
            corridor=None, suspension=None,
            source="test")

        cv1 = validity.ChamberValidity(
            chamber=1, status="valid", max_area=100.0, ref_body_area=90.0,
            body_threshold=45.0, ever_had_body=True, note="",
            occupied_fraction=1.0, unsegmentable_fraction=0.0)
        cv2 = validity.ChamberValidity(
            chamber=2, status="valid", max_area=100.0, ref_body_area=90.0,
            body_threshold=45.0, ever_had_body=True, note="",
            occupied_fraction=1.0, unsegmentable_fraction=0.0)

        tv = validity.TrialValidity(chambers=(cv1, cv2))
        plan = R.TrialPlan(
            chambers=(ch1, ch2), trial_validity=tv, calib_indices=(0, 10, 20),
            warnings=())

        # 模拟 skipped（悬挂点不可估）
        skipped = {
            1: "test-ch1：悬挂点不可估（走廊标定失败 ⇒ 悬挂点不可估（不猜））⇒ 拒绝产出数字",
            2: "test-ch2：悬挂点不可估（走廊标定失败 ⇒ 悬挂点不可估（不猜））⇒ 拒绝产出数字",
        }

        ffmpeg_path, ffmpeg_source = video._resolve_ffmpeg_tool("ffmpeg")
        ffprobe_path, ffprobe_source = video._resolve_ffmpeg_tool("ffprobe")
        run_data = analyze._build_run_json(info, plan, "TST", skipped, set(),
                                           ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source)

        # not_scored 必须存在且非空
        assert "not_scored" in run_data, "run.json 缺少 not_scored 键"
        not_scored = run_data["not_scored"]
        assert isinstance(not_scored, list), "not_scored 必须是列表"
        assert len(not_scored) == 2, f"not_scored 应该包含 2 个跳过的隔间，实际 {len(not_scored)}"

        # 检查是否包含原因原文
        not_scored_chambers = {item["chamber"] for item in not_scored}
        assert not_scored_chambers == {1, 2}, "not_scored 应包含隔间 1 和 2"

        for item in not_scored:
            assert "chamber" in item and "reason" in item
            assert item["chamber"] in skipped
            assert item["reason"] == skipped[item["chamber"]]


# ---- 补充：not_scored 为空时也要有这个键 -----------------------------------


def test_runjson_not_scored_empty_but_present() -> None:
    """not_scored 为空时也要有这个键（写 []），不许省略。"""
    with mock.patch.dict("os.environ", {"DPX_FFMPEG": "/fake/ffmpeg", "DPX_FFPROBE": "/fake/ffprobe"}), \
         mock.patch("pathlib.Path.exists", return_value=True):
        # 全部正常隔间（MOVE 和 STILL）
        kinds = (MOVE, STILL, MOVE, STILL)
        calib = _frames(kinds, 8)
        frames = _frames(kinds, 20)

        plan, reports, skipped = R.analyze_frames(
            calib, frames, fps=FPS, assay="TST", trial_prefix="test", n_chambers=4)

        # 应该没有 skipped
        assert len(skipped) == 0, "所有隔间应该都产出数字"

        from depressionplex import video
        info = video.VideoInfo(
            path=Path("/fake.mp4"), fps=FPS, n_frames=len(frames),
            width=400, height=H, frame_count_source="nb_frames")

        ffmpeg_path, ffmpeg_source = video._resolve_ffmpeg_tool("ffmpeg")
        ffprobe_path, ffprobe_source = video._resolve_ffmpeg_tool("ffprobe")
        run_data = analyze._build_run_json(info, plan, "TST", skipped, set(reports),
                                           ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source)

        # not_scored 必须存在
        assert "not_scored" in run_data, \
            "run.json 缺少 not_scored 键（为空时也要有，写 []）"
        assert run_data["not_scored"] == [], "not_scored 应该是空列表"


# ---- 判据 4 补强：进了 CSV 的隔间不许在 run.json 里再写一遍有效性 ----------


def test_runjson_omits_validity_of_scored_chambers() -> None:
    """产出了 CSV 行的隔间，不许出现在 run.json 的 chamber_validity 里。

    只比最外层键名（判据 4 的原写法）抓不到这条：CSV 那列叫 `validity_status`，
    run.json 这边叫 `chamber_validity[].status`，**键名不同、数字同源不同路**
    ——CSV 取 `TrialReport`，run.json 取 `plan.trial_validity`，是两个对象。
    所以这里按隔间号比，不按键名比。
    """
    with mock.patch.dict("os.environ", {"DPX_FFMPEG": "/fake/ffmpeg", "DPX_FFPROBE": "/fake/ffprobe"}), \
         mock.patch("pathlib.Path.exists", return_value=True):
        kinds = (MOVE, STILL, MOVE, STILL)
        calib = _frames(kinds, 8)
        frames = _frames(kinds, 20)

        plan, reports, skipped = R.analyze_frames(
            calib, frames, fps=FPS, assay="TST", trial_prefix="test", n_chambers=4)
        assert reports, "这批帧应当至少有一个隔间产出数字，否则本测试没在测东西"

        from depressionplex import video
        info = video.VideoInfo(
            path=Path("/fake.mp4"), fps=FPS, n_frames=len(frames),
            width=400, height=H, frame_count_source="nb_frames")

        scored = {int(k) for k in reports}
        ffmpeg_path, ffmpeg_source = video._resolve_ffmpeg_tool("ffmpeg")
        ffprobe_path, ffprobe_source = video._resolve_ffmpeg_tool("ffprobe")
        run_data = analyze._build_run_json(info, plan, "TST", skipped, scored,
                                           ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source)

        listed = {item["chamber"] for item in run_data["chamber_validity"]}
        dup = listed & scored
        assert not dup, (
            f"隔间 {sorted(dup)} 既有 CSV 行又在 run.json 的 chamber_validity 里。"
            "一个数字只许有一个序列化器（架构 §3.5 / DP-054）")

        # 反面：没产出数字的隔间**必须**还在（它没有 CSV 行，有效性只能在这里说）
        unscored = {cv.chamber for cv in plan.trial_validity.chambers} - scored
        assert listed == unscored, (
            f"chamber_validity 应当正好是没进 CSV 的隔间 {sorted(unscored)}，"
            f"实际 {sorted(listed)}")


def test_build_run_json_requires_scored_argument() -> None:
    """`scored` 必须是**没有默认值**的参数。

    给它一个默认值（比如 `scored=()`）就等于把上一条守卫做成装饰：
    忘了传的调用点会安静地退回「全部隔间都写」，也就是又回到两个序列化器。
    """
    import inspect

    sig = inspect.signature(analyze._build_run_json)
    assert "scored" in sig.parameters, "_build_run_json 少了 scored 参数"
    assert sig.parameters["scored"].default is inspect.Parameter.empty, \
        "scored 不许有默认值——漏传时必须报错，不许安静退回旧行为"


# ---- 补充：版本号只认 [project] 表 ----------------------------------------


def test_version_only_from_project_table() -> None:
    """`_version_from_pyproject` 只认 `[project]` 表里的 version。

    原写法 `line.startswith("version")` 命中任意表里的第一行 version——
    在 `[tool.某某]` 下面随手写一行就能改掉 run.json 记的版本号，
    而 tool_version 是「这批数字是哪份代码算的」的唯一凭据。
    """
    f = analyze._version_from_pyproject

    assert f('[project]\nname = "x"\nversion = "1.2.3"\n') == "1.2.3"
    assert f("[project]\nversion = '0.1.0.dev0'\n") == "0.1.0.dev0"
    # 行尾注释要剥掉
    assert f('[project]\nversion = "1.0"  # 别动\n') == "1.0"

    # [tool.…] 里的 version 不许被认（哪怕它排在前面）
    assert f('[tool.poetry]\nversion = "9.9.9"\n[project]\nversion = "1.2.3"\n') == "1.2.3"
    assert f('[tool.poetry]\nversion = "9.9.9"\n') is None

    # 没有 version / 没有 [project] / 空文件 ⇒ None（由调用方决定怎么退化并说出来）
    assert f('[project]\nname = "x"\n') is None
    assert f("") is None
    # 键名只是以 version 开头的其它键不算
    assert f('[project]\nversion_scheme = "pep440"\n') is None


def test_tool_version_is_real_in_this_repo() -> None:
    """在本仓里跑，tool_version 必须是真版本号，不许是 "unknown"。

    这条是给「静默兜底」设的闸：pyproject.toml 就在源码树里，
    此处取不到说明取版本号那段坏了，而它坏掉的表现恰好是一个看起来正常的字符串。
    """
    v = analyze._get_tool_version()
    assert v != "unknown", "在本仓里都取不到版本号，说明 _get_tool_version 坏了"
    assert v[0].isdigit(), f"版本号形状不对：{v!r}"


# ---- 补充：总帧数未知时传 None，不许传 0 -----------------------------------


def test_progress_total_is_none_when_unknown() -> None:
    """`analyze_frames` 这条路总帧数未知 ⇒ 回调第二个参数必须是 None。

    这一层只见 `Iterable`，数不出总数。传 0 的话下游能拿它当数字用
    （`frame/0`、「共 0 帧」、进度条 100%），和 CSV 里未放行的 immobility 留空
    而不填 0 是同一条纪律：**未知不许长成数字的样子。**
    知道总数的那一层（`analyze_video`）负责补上，见 test_progress_total_filled_in。
    """
    kinds = (MOVE, STILL, MOVE, STILL)
    calib = _frames(kinds, 8)
    frames = _frames(kinds, 12)

    totals = []
    R.analyze_frames(
        calib, frames, fps=FPS, assay="TST", trial_prefix="test",
        n_chambers=4, progress=lambda c, t: totals.append(t))

    assert totals, "进度回调一次都没被调用"
    assert all(t is None for t in totals), \
        f"总帧数未知时必须传 None，实际收到 {sorted(set(map(str, totals)))}"


def test_progress_total_filled_in_by_analyze_video() -> None:
    """`analyze_video` 知道帧数 ⇒ 回调第二个参数必须是真帧数（不是 None、不是 0）。

    这里把 video 的三个入口换成合成帧，其余走真代码——沙箱没有真素材，
    但这条要验的恰恰是「谁负责补总数」这个分工，不需要真解码。
    """
    from depressionplex import video as V

    kinds = (MOVE, STILL, MOVE, STILL)
    frames = _frames(kinds, 12)
    h, w = frames[0].shape
    info = V.VideoInfo(path=Path("/合成.mp4"), fps=FPS, n_frames=len(frames),
                       width=w, height=h, frame_count_source="packets")

    orig = (V.probe, V.frames_at, V.iter_gray)
    V.probe = lambda path: info
    V.frames_at = lambda i, idx: [frames[k] for k in idx]
    V.iter_gray = lambda i: iter(frames)
    try:
        seen = []
        R.analyze_video("/合成.mp4", assay="TST", n_chambers=4,
                        progress=lambda c, t: seen.append((c, t)))
    finally:
        V.probe, V.frames_at, V.iter_gray = orig

    assert seen, "进度回调一次都没被调用"
    assert all(t == len(frames) for _, t in seen), \
        f"总帧数应当全是 {len(frames)}，实际 {sorted(set(t for _, t in seen))}"
    assert seen[-1][0] == len(frames), f"最后一次的已处理帧数应为 {len(frames)}"


# ---- 补充：序列化不许把「量出来是 0」写成 null -----------------------------


def test_runjson_keeps_zero_bl_est_distinct_from_none() -> None:
    """`bl_est` 为 0.0 时 run.json 必须写 0.0，不许写 null。

    `if bl_est else None`（原写法）把「量出来是 0」和「量不出来」抹成同一件事。
    **说明**：现在的生产者 `segment.py` 会先做 `bl_est if bl_est > 0 else None`
    归一化，所以真数据里到不了 0.0——这条守的是序列化器本身不许自作归一化，
    免得哪天生产者改了口径（比如允许 0 表示"贴着地板"），信息在这一层被吃掉。
    """
    with mock.patch.dict("os.environ", {"DPX_FFMPEG": "/fake/ffmpeg", "DPX_FFPROBE": "/fake/ffprobe"}), \
         mock.patch("pathlib.Path.exists", return_value=True):
        from depressionplex import video
        from depressionplex.assay_core import segment, validity

        info = video.VideoInfo(
            path=Path("/fake.mp4"), fps=FPS, n_frames=10,
            width=400, height=H, frame_count_source="nb_frames")

        def _plan(bl):
            corr = segment.TapeCorridor(
                col_range=(44, 49), row_range=(70, 224), confidence=1.0,
                band_range=(70, 224), sealed=True, bl_est=bl)
            ch = R.ChamberPlan(index=1, col_range=(0, 95), corridor=corr,
                               suspension=(46.5, 70.0), source="test")
            cv = validity.ChamberValidity(
                chamber=1, status="valid", max_area=100.0, ref_body_area=90.0,
                body_threshold=45.0, ever_had_body=True, note=None,
                occupied_fraction=1.0, unsegmentable_fraction=0.0)
            return R.TrialPlan(chambers=(ch,), trial_validity=validity.TrialValidity(
                chambers=(cv,)), calib_indices=(0, 5), warnings=())

        ffmpeg_path, ffmpeg_source = video._resolve_ffmpeg_tool("ffmpeg")
        ffprobe_path, ffprobe_source = video._resolve_ffmpeg_tool("ffprobe")

        zero = analyze._build_run_json(info, _plan(0.0), "TST", {}, set(),
                                        ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source)
        assert zero["chambers"][0]["corridor"]["bl_est"] == 0.0, \
            "bl_est=0.0 被写成了 null——「量出来是 0」和「量不出来」不是一件事"

        none = analyze._build_run_json(info, _plan(None), "TST", {}, set(),
                                        ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source)
        assert none["chambers"][0]["corridor"]["bl_est"] is None


def test_runjson_keeps_note_none_distinct_from_empty() -> None:
    """`note` 为 None（没话说）不许被写成 ""（有话说但是空串）。"""
    with mock.patch.dict("os.environ", {"DPX_FFMPEG": "/fake/ffmpeg", "DPX_FFPROBE": "/fake/ffprobe"}), \
         mock.patch("pathlib.Path.exists", return_value=True):
        from depressionplex import video
        from depressionplex.assay_core import validity

        info = video.VideoInfo(
            path=Path("/fake.mp4"), fps=FPS, n_frames=10,
            width=400, height=H, frame_count_source="nb_frames")

        def _plan(note):
            ch = R.ChamberPlan(index=1, col_range=(0, 95), corridor=None,
                               suspension=None, source="test")
            cv = validity.ChamberValidity(
                chamber=1, status="valid", max_area=100.0, ref_body_area=90.0,
                body_threshold=45.0, ever_had_body=True, note=note,
                occupied_fraction=1.0, unsegmentable_fraction=0.0)
            return R.TrialPlan(chambers=(ch,), trial_validity=validity.TrialValidity(
                chambers=(cv,)), calib_indices=(0, 5), warnings=())

        ffmpeg_path, ffmpeg_source = video._resolve_ffmpeg_tool("ffmpeg")
        ffprobe_path, ffprobe_source = video._resolve_ffmpeg_tool("ffprobe")

        # 隔间 1 没进 CSV（scored 为空）⇒ 它的有效性写在 run.json 里
        got_none = analyze._build_run_json(info, _plan(None), "TST", {}, set(),
                                           ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source)
        assert got_none["chamber_validity"][0]["note"] is None, \
            "note=None 被写成了空串——下游分不出「没备注」和「备注是空串」"

        got_empty = analyze._build_run_json(info, _plan(""), "TST", {}, set(),
                                            ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source)
        assert got_empty["chamber_validity"][0]["note"] == ""


# ---- 补充：run.json 必须带实际生效的 FROZEN 判定参数（§3.5 点名 θ_mob）-----


def test_runjson_carries_effective_rules_params() -> None:
    """run.json 必须带 θ_mob 等实际生效的判定参数，且**现读 dataclass**。

    为什么必须有：一份 immobility 秒数离开本机之后，"凭什么这么算"全靠这几个数
    ——B6 的报告按判据要印 θ_mob，审计包要能复现。派工单 §3.5 点名要它，原交付漏了。

    为什么不许在 analyze.py 里抄字面量：抄一份就有了第二个真值，
    改了 `TstRulesParams` 而忘了改这里，run.json 会**理直气壮地记错**
    （比 KeyError 危险得多，因为它看起来完全正常）。
    """
    with mock.patch.dict("os.environ", {"DPX_FFMPEG": "/fake/ffmpeg", "DPX_FFPROBE": "/fake/ffprobe"}), \
         mock.patch("pathlib.Path.exists", return_value=True):
        from depressionplex import video
        from depressionplex.assay_core import rules as RU, validity

        info = video.VideoInfo(
            path=Path("/fake.mp4"), fps=FPS, n_frames=10,
            width=400, height=H, frame_count_source="nb_frames")
        ch = R.ChamberPlan(index=1, col_range=(0, 95), corridor=None,
                           suspension=None, source="test")
        cv = validity.ChamberValidity(
            chamber=1, status="valid", max_area=100.0, ref_body_area=90.0,
            body_threshold=45.0, ever_had_body=True, note=None,
            occupied_fraction=1.0, unsegmentable_fraction=0.0)
        plan = R.TrialPlan(chambers=(ch,), trial_validity=validity.TrialValidity(
            chambers=(cv,)), calib_indices=(0, 5), warnings=())

        ffmpeg_path, ffmpeg_source = video._resolve_ffmpeg_tool("ffmpeg")
        ffprobe_path, ffprobe_source = video._resolve_ffmpeg_tool("ffprobe")
        got = analyze._build_run_json(info, plan, "TST", {}, set(),
                                       ffmpeg_path, ffmpeg_source, ffprobe_path, ffprobe_source)
    assert "rules" in got, "run.json 缺少 rules（θ_mob 等 FROZEN 判定参数）"

    frozen = RU.TstRulesParams()
    assert got["rules"]["theta_mob"] == frozen.theta_mob, \
        "run.json 里的 θ_mob 与 TstRulesParams 不一致——两个真值必然漂移"

    # 字段集必须与 dataclass 完全一致：往 TstRulesParams 加一个阈值而没在
    # run.json 里露出来，这条就红——不许有"生效了但不记录"的判定参数。
    want = {f.name for f in dataclasses.fields(frozen)}
    assert set(got["rules"]) == want, \
        f"rules 字段集与 TstRulesParams 不符，缺 {sorted(want - set(got['rules']))}，" \
        f"多 {sorted(set(got['rules']) - want)}"

    # 必须能 json 序列化（tuple 字段会变 list，别有 numpy 标量混进来）
    json.loads(json.dumps(got["rules"], ensure_ascii=False))

    # analyze.py 里不许出现 θ_mob 的字面量
    src = (Path(analyze.__file__)).read_text(encoding="utf-8")
    assert str(frozen.theta_mob) not in src, \
        f"analyze.py 里出现了 θ_mob 的字面量 {frozen.theta_mob}——现读 dataclass，别抄"


def test_analysis_path_has_no_rules_override() -> None:
    """分析入口不许有 `params`/`rules` 覆盖口。

    上一条把 run.json 的 rules 写成"现读 FROZEN 默认值"，成立的前提是**跑的时候
    用的就是默认值**。哪天给 `analyze_video` 加了 `params=` 让调用方换阈值，
    run.json 就会记着 FROZEN 值、而数字是用别的阈值算的——这条守卫让那次改动
    必须同时把参数穿进 run.json，而不是等到有人对不上账。
    """
    import inspect

    for fn in (R.analyze_video, R.analyze_frames, R.segment_series):
        names = set(inspect.signature(fn).parameters)
        bad = names & {"params", "rules", "theta_mob", "rules_params"}
        assert not bad, (
            f"{fn.__name__} 新增了判定参数入口 {sorted(bad)}："
            "要么撤掉，要么把实际生效的参数穿进 _build_run_json 的 rules 里"
            "（见 test_runjson_carries_effective_rules_params）")

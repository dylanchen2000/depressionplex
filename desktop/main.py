"""DEPRESSION-PLEX 桌面外壳入口。

启动方式**只有一种**（架构 §3.4）：仓根为 cwd，`python -m desktop.main`；
`--self-test` 是无显示自检，CI 的唯一判据。

退出码：0 = 正常 / 自检通过；2 = 自检不通过（与引擎 CLI 的 2 同义：判据不满足）。
"""

import os
import sys
import time

import PySide6
from PySide6.QtWidgets import QApplication

from desktop.app.main_window import PAGE_CLASSES, MainWindow, PAGE_ORDER
from desktop.app.utils.paths import resource_path, user_data_dir
from desktop.app.utils.stdio import force_utf8

STYLESHEET = "app/styles/dark.qss"


def load_stylesheet(app: QApplication) -> bool:
    """套上暗色皮肤。返回是否加载成功。"""
    path = resource_path(STYLESHEET)
    if not path.exists():
        return False
    app.setStyleSheet(path.read_text(encoding="utf-8"))
    return True


def self_test() -> int:
    """无显示自检：逐个构造页面、核对页面清单、打印耗时，**不起窗不进事件循环**。

    任何一条不满足都返回 2。**尤其是皮肤缺失也算不通过**——`dark.qss` 是打包时
    靠 spec 的 `datas` 带进去的，漏了它产品能启动但一身默认灰皮，
    而「能启动」正是最容易被当成通过的那种失败（B10 改 spec 时最容易踩）。
    """
    force_utf8()
    t0 = time.perf_counter()
    app = QApplication(sys.argv)
    problems: list[str] = []

    print(f"PySide6 {PySide6.__version__}")
    print(f"QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM', '(未设置)')}")
    # 只算路径不建目录：自检是诊断，不该在客户机上留下任何东西
    print(f"user_data_dir={user_data_dir(create=False)}")

    if load_stylesheet(app):
        print(f"皮肤已加载 {STYLESHEET}")
    else:
        problems.append(f"皮肤缺失：{resource_path(STYLESHEET)}")

    # 逐个单独构造，时间才是真的量出来的；顺带证明每个页面**不依赖父窗口**也能构造。
    #
    # 类名一律从 `main_window.PAGE_CLASSES` 取，**不许再从某个页面模块 getattr**（DP-101 修）：
    # 原来这里写的是 `getattr(placeholders, cls_name)`，等于给「哪个类是哪一页」开了
    # 第二个解析器。页面从 placeholders 搬到自己的模块（这是每一页最终都要走的路）时，
    # 主窗口用新类、自检还在老模块里找，于是自检以 AttributeError **崩掉**（退 1），
    # 而不是「不通过」（退 2）—— 归因时先怀疑新页面本身，方向就错了。
    print("逐页构造：")
    for name, cls_name in PAGE_ORDER:
        cls = PAGE_CLASSES.get(cls_name)
        if cls is None:
            problems.append(f"页面类 {cls_name} 不在 PAGE_CLASSES 里（PAGE_ORDER 与类表脱节）")
            continue
        t = time.perf_counter()
        cls()
        print(f"  {name} ({cls_name}) {(time.perf_counter() - t) * 1000:.2f} ms")

    window = MainWindow()
    got = tuple(window.pages)
    want = tuple(name for name, _ in PAGE_ORDER)
    if got != want:
        problems.append(f"页面清单不符：期望 {want}，实际 {got}")

    # 模式徽章必须真的挂在状态栏上（B8 / DP-111）。沙箱里没有 PySide6，
    # 只有这里能验「它确实是主窗口的一个子控件」——AST 守卫只能看见代码写了这句，
    # 看不见它有没有生效。**这一条是徽章唯一的运行期证明。**
    # **直接取属性，不用 `getattr(..., None)`**：这个函数里一律不许出现按名字找东西的
    # 写法（`test_page_classes_resolved_in_one_place` 连 `getattr`/`globals`/`vars` 一起禁了，
    # 起因是 DP-101 那个「按名字找页面类」的第二解析器）。那条守卫的措辞比它要防的事宽，
    # 但**宽不是错**：这里本来就该直接读——`MainWindow.__init__` 无条件设这个属性，
    # 读不到就是接线被人拆了，那时一个 AttributeError 当场炸掉比一句温和的提示更对。
    badge = window.mode_badge
    if badge.parent() is None:
        problems.append("mode_badge 没有被加进任何容器（不会显示）")
    elif not window.statusBar().isAncestorOf(badge):
        # **「有父容器」不等于「在状态栏上」**（B8 复核 R5）：把徽章挂进任意一页的
        # layout，`parent()` 也不是 None，这条断言照样过，而「七页可见」当场就没了——
        # 而七页可见正是本件通篇的主张。用 `isAncestorOf` 不用
        # `badge.parent() is window.statusBar()`：前者是文档化的 API，
        # Qt 在中间塞了容器也照样成立，同时仍然能把「状态栏」和「某一页」分开。
        problems.append(
            f"mode_badge 挂在 {type(badge.parent()).__name__} 里，不在状态栏上："
            "那样只有它所在的那一页看得见")
    else:
        view = badge.view
        print(f"模式徽章：{view.text}（mode={window.calibration_status.mode.value}，"
              f"claims_metrology={view.claims_metrology}）")
        if not view.text.strip():
            problems.append("徽章挂上去了，但上面一个字都没有")
        for reason in window.calibration_status.reasons:
            print(f"  原因：{reason}")
        # 自检**不判断该是黄还是绿**（那取决于随包标定文件，两种都是合法发布态），
        # 只判断「徽章的声称」与「判定模块的结论」一致。这两个数一旦分叉，
        # 就会出现界面写着计量模式而导出按研究版声明（或反过来）的情形。
        if view.claims_metrology != window.calibration_status.may_report_metrology:
            problems.append(
                f"徽章声称 claims_metrology={view.claims_metrology}，"
                f"但判定结论是 {window.calibration_status.may_report_metrology}")

    # ── 实验路径交接 + 结果页多视频选择（真实交互，不是「能构造」）──
    handoff_problems = _probe_experiment_handoff(window, app)
    problems.extend(handoff_problems)

    total_ms = (time.perf_counter() - t0) * 1000
    if problems:
        for p in problems:
            print(f"自检不通过：{p}", file=sys.stderr)
        print(f"SELF-TEST FAILED pages={len(got)} total_ms={total_ms:.2f}")
        return 2

    print(f"SELF-TEST OK pages={len(got)} total_ms={total_ms:.2f}")
    return 0


def _probe_experiment_handoff(window: MainWindow, app: QApplication) -> list[str]:
    """自检：新建实验信号 → 队列加载真实路径；结果页可切换第二段视频。

    若队列仍写死 `test_experiment.json`，或主窗口没接线，本探针必须红。
    """
    import json
    import tempfile
    from pathlib import Path

    from PySide6.QtWidgets import QLabel

    from desktop.app.models.experiment import (
        ExperimentPlan,
        VideoEntry,
        write_experiment_json,
    )
    from desktop.app.utils.paths import user_data_dir

    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        v1 = root / "clip_alpha.mp4"
        v2 = root / "clip_beta.mp4"
        v1.write_bytes(b"")
        v2.write_bytes(b"")
        out = root / "exp_out"
        out.mkdir()
        # 故意放一个诱饵：若代码仍硬编码 user_data_dir/test_experiment.json，
        # 会吃到 note=DECOY…，本探针当场红。
        decoy = {
            "schema_version": "1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "operator": None,
            "note": "DECOY_MUST_NOT_BE_LOADED",
            "assay": "TST",
            "n_chambers": 1,
            "calib_frames": 12,
            "body_area_prior": None,
            "output_dir": str(out),
            "videos": [{"path": str(v1.resolve()), "trial_prefix": None}],
        }
        decoy_path = user_data_dir(create=True) / "test_experiment.json"
        decoy_path.write_text(json.dumps(decoy), encoding="utf-8")
        try:
            plan = ExperimentPlan(
                assay="TST",
                n_chambers=2,
                calib_frames=30,
                body_area_prior=None,
                output_dir=out,
                videos=[
                    VideoEntry(v1, None),
                    VideoEntry(v2, "beta"),
                ],
                operator="self-test",
                note="handoff-probe",
            )
            exp_path = write_experiment_json(plan)

            new_page = window.pages["新建实验"]
            queue = window.pages["分析队列"]
            results = window.pages["结果"]

            # 发信号走主窗口接线（与真实向导完成路径相同）
            new_page.experiment_created.emit(str(exp_path))
            app.processEvents()

            if queue._experiment is None:
                problems.append(
                    "experiment_created 发出后队列未加载实验（主窗口可能没接线）"
                )
                return problems

            note = queue._experiment.get("note")
            if note == "DECOY_MUST_NOT_BE_LOADED":
                problems.append(
                    "队列仍加载了 user_data_dir/test_experiment.json 诱饵，"
                    "没有吃 experiment_created 传来的真实路径"
                )
            if note != "handoff-probe":
                problems.append(
                    f"队列加载的实验 note 不对：{note!r}（期望 handoff-probe）"
                )

            loaded_path = queue._experiment_path
            if loaded_path is None or Path(loaded_path).resolve() != exp_path.resolve():
                problems.append(
                    f"队列 _experiment_path 不是向导写出的路径："
                    f"got={loaded_path!r} want={exp_path}"
                )

            videos = queue._experiment.get("videos") or []
            if len(videos) != 2:
                problems.append(f"队列视频数应为 2，实际 {len(videos)}")
            elif Path(videos[1]["path"]).name != "clip_beta.mp4":
                problems.append(f"第二段视频名不对：{videos[1]!r}")

            if len(queue._items) != 2:
                problems.append(f"队列表项数应为 2，实际 {len(queue._items)}")

            # 结果页：装入同一份契约后必须能切到第二段
            results.set_experiment(queue._experiment, video_index=0)
            app.processEvents()
            if results.video_combo.count() != 2:
                problems.append(
                    f"结果页视频下拉应为 2 项，实际 {results.video_combo.count()}"
                )
            results.video_combo.setCurrentIndex(1)
            app.processEvents()
            if results._current_video_index != 1:
                problems.append(
                    f"结果页切换第二段后 _current_video_index="
                    f"{results._current_video_index}（期望 1）"
                )

            review = window.pages["复核"]
            export_side = window.pages["导出"]
            review_blob = "\n".join(w.text() for w in review.findChildren(QLabel))
            export_blob = "\n".join(w.text() for w in export_side.findChildren(QLabel))
            if "尚未实现" not in review_blob and "占位" not in review_blob:
                problems.append("复核页文案未标明尚未实现/占位")
            if "尚未实现" not in export_blob and "占位" not in export_blob:
                problems.append("侧栏导出页文案未标明尚未实现/占位")
            if "本页由 B5 交付" in review_blob or "本页由 B6 交付" in export_blob:
                problems.append("占位页仍写「本页由 Bx 交付」，对用户不诚实")

            if not problems:
                print(
                    f"实验交接探针：path={exp_path.name} videos={len(videos)} "
                    f"queue_items={len(queue._items)} "
                    f"results_combo={results.video_combo.count()} OK"
                )
        finally:
            try:
                decoy_path.unlink(missing_ok=True)
            except OSError:
                pass

    return problems


def main() -> int:
    force_utf8()
    if "--self-test" in sys.argv[1:]:
        return self_test()

    app = QApplication(sys.argv)
    if not load_stylesheet(app):
        # 正常启动时皮肤缺失不拦人（能用比好看重要），但必须说出来
        print(f"警告：皮肤缺失 {resource_path(STYLESHEET)}", file=sys.stderr)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

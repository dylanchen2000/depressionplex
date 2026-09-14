"""帧源测试。**不带视频素材**——所以只测能纯函数化的部分。

测的是本模块存在的理由本身：不猜 fps、不用 `-ss` 定位、缺元数据就报错。
真素材上的端到端由 `cli/analyze.py` 手跑（`docs/RUNBOOK` 记录实测输出）。
"""

from __future__ import annotations

from pathlib import Path

from depressionplex import video as V

# 全局 mock：沙箱里没有 ffmpeg，decode_cmd 会尝试解析。测试不需要真的解码。
V._resolve_ffmpeg_tool = lambda tool: f"/fake/{tool}"  # type: ignore


def _info(**kw) -> V.VideoInfo:
    d = dict(path=Path("/tmp/x.mp4"), fps=25.0, n_frames=9000, width=400,
             height=268, frame_count_source="nb_frames")
    d.update(kw)
    return V.VideoInfo(**d)                      # type: ignore[arg-type]


def test_parse_rate_never_guesses() -> None:
    """"0/0"（未知帧率）必须是 None。返回 0 会除零，返回 25 会安静地算错
    所有时长——本项目所有阈值都是**秒**。"""
    assert V._parse_rate("25/1") == 25.0
    assert abs(V._parse_rate("30000/1001") - 29.97) < 0.01
    assert V._parse_rate("0/0") is None
    assert V._parse_rate("N/A") is None
    assert V._parse_rate(None) is None
    assert V._parse_rate("") is None


def test_duration_from_measured_frames() -> None:
    assert abs(_info(fps=25.0, n_frames=9000).duration_s - 360.0) < 1e-9


def test_decode_cmd_seeks_by_frame_not_time() -> None:
    """按帧号定位。`-ss` 会落到最近关键帧，误差几帧——而窗口边界
    （TST 全程 / FST 后 4 min）错几帧就是口径事故。"""
    cmd = V.decode_cmd(_info(), start_frame=3000, n_frames=100)
    assert "-ss" not in cmd, "不许按时间跳转"
    assert any(c.startswith("select=gte(n") and "3000" in c for c in cmd), cmd
    assert "-vsync" in cmd and cmd[cmd.index("-vsync") + 1] == "0"
    assert cmd[cmd.index("-frames:v") + 1] == "100"
    # 灰度裸流：解码器直接给单通道，省一次 RGB→gray 转换，也免得色彩矩阵差异
    # 让同一段素材在两台机器上解出不同的灰度值。
    assert cmd[-5:] == ["-f", "rawvideo", "-pix_fmt", "gray", "-"]


def test_decode_cmd_no_filter_when_starting_at_zero() -> None:
    """从头解就别插 filter：select 链会让 ffmpeg 走一遍额外的滤镜图。"""
    cmd = V.decode_cmd(_info())
    assert "-vf" not in cmd
    assert "-frames:v" not in cmd


def test_decode_cmd_rejects_nonsense() -> None:
    for kw in ({"start_frame": -1}, {"n_frames": 0}, {"n_frames": -5}):
        try:
            V.decode_cmd(_info(), **kw)           # type: ignore[arg-type]
        except ValueError:
            pass
        else:
            raise AssertionError(f"{kw} 应被拒绝")


def test_probe_missing_file_raises_video_error() -> None:
    """不存在就报 VideoError，不返回一个"空信息"让上层继续算。"""
    try:
        V.probe("/tmp/绝对不存在的素材-dp041.mp4")
    except V.VideoError as e:
        assert "不存在" in str(e)
    else:
        raise AssertionError("必须 raise VideoError")

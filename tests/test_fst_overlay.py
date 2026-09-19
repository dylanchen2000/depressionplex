"""叠加短片与落盘守卫测试。

两件事在这里钉死：
1. **研究产物不进仓库**——`refuse_in_repo` 对仓库工作树/data//tests/ 一律拒；
2. **半成品不冒充证据**——尺寸不符 raise、0 帧删文件、abort 删半截 mp4、
   编码失败也删文件。

画图函数是纯 numpy，直接断言像素。编码走真 ffmpeg（本机可用）；
万一环境没有 ffmpeg，按本仓 SKIP 约定打印后返回（运行器会把它算进 PASS，
所以交付报告里单列跳过项）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from depressionplex import video
from depressionplex.fst_research import overlay as ov


def _ffmpeg_available() -> bool:
    try:
        video._resolve_ffmpeg_tool(video.TOOL_FFMPEG)
        return True
    except Exception:
        return False


def test_refuse_in_repo_blocks_repo_paths() -> None:
    for bad in (ov.REPO_ROOT / "data" / "x.mp4",
                ov.REPO_ROOT / "tests" / "y.json",
                ov.REPO_ROOT / "诊断.json",
                ov.REPO_ROOT):
        try:
            ov.refuse_in_repo(bad)
        except ValueError as e:
            assert "仓库" in str(e)
        else:
            raise AssertionError(f"仓库内路径没被拒: {bad}")


def test_refuse_in_repo_allows_outside() -> None:
    with tempfile.TemporaryDirectory() as td:
        out = ov.refuse_in_repo(Path(td) / "clip.mp4")
        assert out == Path(td).resolve() / "clip.mp4"
        # 字符串与 ~ 也要吃（CLI 传进来的是用户写法）
        out2 = ov.refuse_in_repo(f"{td}/sub/dir")
        assert str(out2).startswith(str(Path(td).resolve()))


def test_gray_to_rgb() -> None:
    g = np.array([[0, 128], [255, 7]], dtype=np.uint8)
    rgb = ov.gray_to_rgb(g)
    assert rgb.shape == (2, 2, 3)
    assert (rgb[..., 0] == g).all() and (rgb[..., 2] == g).all()


def test_draw_rect_and_hline_pixels() -> None:
    rgb = np.zeros((20, 30, 3), dtype=np.uint8)
    ov.draw_rect(rgb, 5, 10, 12, 20, ov.COLOR_TANK)
    assert tuple(rgb[5, 10]) == ov.COLOR_TANK          # 四角
    assert tuple(rgb[12, 20]) == ov.COLOR_TANK
    assert tuple(rgb[5, 15]) == ov.COLOR_TANK          # 上边
    assert tuple(rgb[8, 10]) == ov.COLOR_TANK          # 左边
    assert tuple(rgb[8, 15]) == (0, 0, 0)              # 内部不填
    ov.draw_hline(rgb, 9, 10, 20, ov.COLOR_WATER)
    assert all(tuple(rgb[9, c]) == ov.COLOR_WATER for c in range(10, 21))


def test_draw_mask_outline_ring_only() -> None:
    rgb = np.zeros((10, 10, 3), dtype=np.uint8)
    m = np.zeros((10, 10), dtype=bool)
    m[3:6, 3:6] = True                                  # 3×3 实心块
    ov.draw_mask_outline(rgb, m, ov.COLOR_ANIMAL)
    hits = [(r, c) for r in range(10) for c in range(10)
            if tuple(rgb[r, c]) == ov.COLOR_ANIMAL]
    assert (4, 4) not in hits                           # 中心被腐蚀掉
    assert len(hits) == 8                               # 只画一圈边
    ov.draw_mask_outline(rgb, np.zeros((10, 10), bool), ov.COLOR_ANIMAL)
    # 空掩膜不炸、不加像素


def test_draw_text_known_glyph_and_fallback() -> None:
    rgb = np.zeros((12, 40, 3), dtype=np.uint8)
    ov.draw_text(rgb, 2, 3, "E", ov.COLOR_TEXT)
    glyph = ov._FONT["E"]
    for dr, row in enumerate(glyph):
        for dc, bit in enumerate(row):
            px = tuple(rgb[2 + dr, 3 + dc])
            expect = ov.COLOR_TEXT if bit == "1" else (0, 0, 0)
            assert px == expect, (dr, dc)
    # 小写映射到大写；认不出的字符画 '?'
    rgb2 = np.zeros((12, 40, 3), dtype=np.uint8)
    ov.draw_text(rgb2, 2, 3, "e")
    assert (rgb2 == rgb).all()
    rgb3 = np.zeros((12, 40, 3), dtype=np.uint8)
    ov.draw_text(rgb3, 0, 0, "é")
    q = np.zeros((12, 40, 3), dtype=np.uint8)
    ov.draw_text(q, 0, 0, "?")
    assert (rgb3 == q).all()


def test_draw_text_outlined_has_black_halo() -> None:
    rgb = np.full((12, 40, 3), 200, dtype=np.uint8)     # 亮背景
    ov.draw_text_outlined(rgb, 4, 4, "C1", ov.COLOR_ANIMAL)
    assert tuple(rgb[4, 4 - 1]) == ov.COLOR_TEXT_BG     # 左侧 1px 黑描边


def test_clip_writer_roundtrip_real_ffmpeg() -> None:
    if not _ffmpeg_available():
        print("  SKIP  test_clip_writer_roundtrip_real_ffmpeg（无 ffmpeg）")
        return
    frames = [np.full((16, 32, 3), v, dtype=np.uint8) for v in (30, 90, 150, 210)]
    with tempfile.TemporaryDirectory() as td:
        out = ov.encode_clip(frames, out_path=Path(td) / "c.mp4",
                             fps=5.0, size=(32, 16))
        assert out.exists() and out.stat().st_size > 100
        info = video.probe(out)                          # 编出来的真是视频
        assert (info.width, info.height) == (32, 16)
        assert info.n_frames == 4


def test_clip_writer_wrong_size_raises() -> None:
    if not _ffmpeg_available():
        print("  SKIP  test_clip_writer_wrong_size_raises（无 ffmpeg）")
        return
    with tempfile.TemporaryDirectory() as td:
        w = ov.ClipWriter(Path(td) / "bad.mp4", fps=5.0, size=(32, 16))
        try:
            w.write(np.zeros((16, 31, 3), dtype=np.uint8))   # 宽差 1px
        except ValueError as e:
            assert "尺寸" in str(e)
        else:
            raise AssertionError("尺寸不符没被拒——ffmpeg 会把后续帧全部错位解释")
        finally:
            w.abort()
        assert not (Path(td) / "bad.mp4").exists()


def test_clip_writer_zero_frames_removes_file() -> None:
    if not _ffmpeg_available():
        print("  SKIP  test_clip_writer_zero_frames_removes_file（无 ffmpeg）")
        return
    with tempfile.TemporaryDirectory() as td:
        w = ov.ClipWriter(Path(td) / "empty.mp4", fps=5.0, size=(32, 16))
        try:
            w.close()
        except (ValueError, video.VideoError):
            pass                       # 0 帧：ffmpeg 报 empty 或本类自己拒
        else:
            raise AssertionError("0 帧短片居然 close 成功——空片不许冒充证据")
        assert not (Path(td) / "empty.mp4").exists()


def test_clip_writer_abort_removes_partial() -> None:
    if not _ffmpeg_available():
        print("  SKIP  test_clip_writer_abort_removes_partial（无 ffmpeg）")
        return
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "partial.mp4"
        w = ov.ClipWriter(out, fps=5.0, size=(32, 16))
        w.write(np.zeros((16, 32, 3), dtype=np.uint8))
        w.abort()                      # 解码中途失败的路径
        assert not out.exists()


def test_clip_writer_refuses_repo_path() -> None:
    try:
        ov.ClipWriter(ov.REPO_ROOT / "data" / "clip.mp4", fps=5.0, size=(8, 8))
    except ValueError as e:
        assert "仓库" in str(e)
    else:
        raise AssertionError("ClipWriter 接受了仓库内落点")

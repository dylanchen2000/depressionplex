"""叠加短片导出：先证明我们看的是**对的动物区域**（Spec A §5.1）。

诊断数字说"第 3250 帧杯 2 面积 412 px"，人没法核；但一段画了杯框、水线、
动物轮廓和帧号的短片，人一眼就能看出我们框的是不是那只老鼠、水线画在哪。
所以叠加短片是**验收物**，不是装饰：每帧烧进 `C<杯号> F<帧号> T<秒> <状态>`。

落盘纪律（与 DP-135 探针同一条）：**研究产物不进仓库**。
`refuse_in_repo` 对仓库工作树、`data/`、`tests/` 一律拒绝——
这些短片是诊断证据，进仓库就会被误当交付物或进验收路径。

编码走 ffmpeg 子进程，工具解析只许经 `video._resolve_ffmpeg_tool`
（守卫 A14：字面量工具名只许出现在 video.py）。仓库不依赖 cv2/imageio，
这里也不引入：灰度帧 → RGB24 裸流 → libx264，全程 numpy 画。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .. import video

REPO_ROOT = Path(__file__).resolve().parents[2]

COLOR_TANK = (80, 220, 120)         # 分析 ROI（含线上留白，R2-115 G1）
COLOR_WATER = (90, 200, 255)        # 水线
COLOR_WATER_BODY = (150, 120, 255)  # 水体候选区（复核 §9：ROI/水体/水线分开画）
COLOR_ANIMAL = (255, 90, 60)
COLOR_UNCLEAR = (255, 200, 40)
COLOR_ABSENT = (160, 160, 160)
COLOR_TEXT = (255, 255, 255)
COLOR_TEXT_BG = (0, 0, 0)

QUALITY_COLORS = {
    "observed": COLOR_ANIMAL,
    "unclear": COLOR_UNCLEAR,
    "lost_short": COLOR_UNCLEAR,
    "declared_absent": COLOR_ABSENT,
}

# 3×5 点阵字模。0 与 O 同形（3 px 宽放不下斜杠零），标签里数字只出现在
# 帧号/秒数位置，不与字母混排；认不出的字符画 '?'。
_FONT: dict[str, tuple[str, ...]] = {
    "0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"), "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "001", "010", "010"),
    "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "111"),
    "A": ("010", "101", "111", "101", "101"), "B": ("110", "101", "110", "101", "110"),
    "C": ("011", "100", "100", "100", "011"), "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "111", "100", "111"), "F": ("111", "100", "111", "100", "100"),
    "G": ("011", "100", "101", "101", "011"), "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"), "J": ("001", "001", "001", "101", "111"),
    "K": ("101", "101", "110", "101", "101"), "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "101", "101", "101"), "N": ("101", "111", "111", "101", "101"),
    "O": ("111", "101", "101", "101", "111"), "P": ("111", "101", "111", "100", "100"),
    "Q": ("111", "101", "101", "111", "001"), "R": ("111", "101", "111", "101", "101"),
    "S": ("011", "100", "010", "001", "110"), "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"), "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "111", "111", "101"), "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"), "Z": ("111", "001", "010", "100", "111"),
    " ": ("000", "000", "000", "000", "000"), ".": ("000", "000", "000", "000", "010"),
    ":": ("000", "010", "000", "010", "000"), "-": ("000", "000", "111", "000", "000"),
    "_": ("000", "000", "000", "000", "111"), "/": ("001", "001", "010", "100", "100"),
    "=": ("000", "111", "000", "111", "000"), "?": ("111", "001", "011", "000", "010"),
}
_GLYPH_UNKNOWN = _FONT["?"]


def refuse_in_repo(path) -> Path:
    """研究产物落点守卫。仓库工作树 / data/ / tests/ 一律拒绝。"""
    p = Path(path).expanduser().resolve()
    try:
        rel = p.relative_to(REPO_ROOT)
    except ValueError:
        return p
    raise ValueError(
        f"研究产物不许落进仓库：{p}（相对仓库 {rel}）。"
        "叠加短片与诊断 JSON 是研究证据，进仓库会被误当交付物/进验收路径"
        "（同 DP-135 探针的纪律）。请给一个仓库外的目录。")


def gray_to_rgb(gray: np.ndarray) -> np.ndarray:
    g = np.asarray(gray, dtype=np.uint8)
    return np.stack([g, g, g], axis=-1)


def _put(rgb: np.ndarray, r: int, c: int, color) -> None:
    h, w, _ = rgb.shape
    if 0 <= r < h and 0 <= c < w:
        rgb[r, c] = color


def draw_rect(rgb: np.ndarray, r0: int, c0: int, r1: int, c1: int, color) -> None:
    for c in range(c0, c1 + 1):
        _put(rgb, r0, c, color)
        _put(rgb, r1, c, color)
    for r in range(r0, r1 + 1):
        _put(rgb, r, c0, color)
        _put(rgb, r, c1, color)


def draw_hline(rgb: np.ndarray, r: int, c0: int, c1: int, color) -> None:
    for c in range(c0, c1 + 1):
        _put(rgb, r, c, color)


def draw_mask_outline(rgb: np.ndarray, mask: np.ndarray, color) -> None:
    m = np.asarray(mask, dtype=bool)
    if not m.any():
        return
    inner = m.copy()
    inner[1:, :] &= m[:-1, :]
    inner[:-1, :] &= m[1:, :]
    inner[:, 1:] &= m[:, :-1]
    inner[:, :-1] &= m[:, 1:]
    edge = m & ~inner
    ys, xs = np.nonzero(edge)
    h, w, _ = rgb.shape
    for y, x in zip(ys, xs):
        if 0 <= y < h and 0 <= x < w:
            rgb[y, x] = color


def draw_text(rgb: np.ndarray, r: int, c: int, text: str, color=COLOR_TEXT,
              scale: int = 1) -> None:
    """3×5 点阵文本，带 1 px 黑描边（亮背心上白字不描边看不见）。"""
    x = c
    for ch in text.upper():
        glyph = _FONT.get(ch, _GLYPH_UNKNOWN)
        for dr, row in enumerate(glyph):
            for dc, bit in enumerate(row):
                if bit == "1":
                    for sr in range(scale):
                        for sc in range(scale):
                            _put(rgb, r + dr * scale + sr, x + dc * scale + sc, color)
        x += 4 * scale


def draw_text_outlined(rgb: np.ndarray, r: int, c: int, text: str,
                       color=COLOR_TEXT, scale: int = 1) -> None:
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr or dc:
                draw_text(rgb, r + dr, c + dc, text, COLOR_TEXT_BG, scale)
    draw_text(rgb, r, c, text, color, scale)


class ClipWriter:
    """流式写一段叠加短片。研究入口是逐帧流式的，短片也得流式写。

    半帧/尺寸不符直接 raise：尺寸写错会让 ffmpeg 把后续所有帧错位解释，
    产出一段"看起来在动"的垃圾短片——那比没有短片更坏。
    `close()` 时 0 帧 ⇒ 删掉空文件并 raise：空短片不许冒充证据。
    """

    def __init__(self, out_path, *, fps: float, size: tuple[int, int]) -> None:
        self.out = refuse_in_repo(out_path)
        # R2-115 P组：目标已存在 ⇒ 拒绝，不 spawn ffmpeg。下面的 -y 只为
        # 避免 ffmpeg 交互提示挂住进程——有了这道守卫，它永远碰不到旧证据。
        if self.out.exists():
            raise FileExistsError(
                f"拒绝覆盖已有研究证据: {self.out}（叠加短片是证据不是草稿；"
                "新一轮运行请写新的 run 目录）")
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self._w, self._h = size
        self._n = 0
        ffmpeg, _ = video._resolve_ffmpeg_tool(video.TOOL_FFMPEG)
        cmd = [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{self._w}x{self._h}", "-r", f"{fps}", "-i", "-",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(self.out)]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, rgb: np.ndarray) -> None:
        arr = np.ascontiguousarray(rgb, dtype=np.uint8)
        if arr.shape != (self._h, self._w, 3):
            raise ValueError(f"叠加帧尺寸 {arr.shape} ≠ 声明的 {(self._h, self._w, 3)}")
        assert self._proc.stdin is not None
        self._proc.stdin.write(arr.tobytes())
        self._n += 1

    def abort(self) -> None:
        """解码中途失败时弃掉这段短片：半截 mp4 不许留在磁盘上冒充证据。"""
        if self._proc.poll() is None:
            self._proc.kill()
        self._proc.wait()
        self.out.unlink(missing_ok=True)

    def close(self) -> Path:
        assert self._proc.stdin is not None
        self._proc.stdin.close()
        err = self._proc.stderr.read().decode(errors="replace") if self._proc.stderr else ""
        rc = self._proc.wait()
        if rc != 0:
            self.out.unlink(missing_ok=True)   # 半截 mp4 不许留在磁盘上冒充证据
            raise video.VideoError(
                f"叠加短片编码失败（exit {rc}，已写 {self._n} 帧）：{err[:400]}")
        if self._n == 0:
            self.out.unlink(missing_ok=True)
            raise ValueError("叠加短片 0 帧——不产出空短片冒充证据")
        return self.out


def encode_clip(rgb_frames, *, out_path, fps: float, size: tuple[int, int]) -> Path:
    """整段喂法的便利包装（测试用）：可迭代的 (h, w, 3) uint8 数组 → mp4。"""
    w = ClipWriter(out_path, fps=fps, size=size)
    for fr in rgb_frames:
        w.write(fr)
    return w.close()

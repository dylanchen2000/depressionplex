"""P2 采集参考读数（DP-116 / DP-109 B7）。

2026-08-24 实测，素材 10mg 2周.mp4 第 600-607 帧。
素材不在仓里，沙箱与 CI 均无此素材——这份常量是「已知良品」的冻结凭证。
SHA256 当时未记录，写 None 不许编。
解码器版本未知（DP-071 教训），scripts/verify_p2_reference.py 复现时写进输出。

随引擎包走：冻结成 exe 后 tests/ 不存在，故不从 tests/fixtures/ 读。
"""
from __future__ import annotations

P2_REFERENCE: dict = {
    "_note": (
        "P2 硬门实测（2026-08-24）。素材不在仓里也不许进仓（大文件规矩）。"
        "沙箱和 CI 均无此素材，参考读数在本环境未复现——"
        "verify_p2_reference.py 只能在有素材的机器上跑。SHA256 当前未知，写 null。"
    ),
    "_decoder_note": (
        "读数产生时的解码器身份未记录（见 DP-071 教训）。"
        "scripts/verify_p2_reference.py 复现时会把当时用的解码器版本写进输出，"
        "以便日后对账。若数值不符，第一嫌疑是解码器差异而不是算法变了。"
    ),
    "source_video": "10mg 2周.mp4",
    "frames_start": 600,
    "frames_end": 607,
    "width": 476,
    "height": 268,
    "fps": 25.0,
    "total_frames": 9661,
    "sha256": None,
    "measured_at": "2026-08-24",
    "decoder": {
        "ffmpeg_version": None,
        "_note": (
            "解码器版本未知（2026-08-24 实测时未记录）。不许编造。"
            "见 DP-071：不同版本 ffmpeg 会解出不同帧，导致读数漂移。"
        ),
    },
    "contrast_abs": 208.5,
    "contrast_ratio": 6.77,
    "noise_floor_px": 0.43,
    "area_jitter_p90": 0.0035,
}

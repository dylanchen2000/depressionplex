#!/usr/bin/env python3
"""下载并校验 LGPL 版 ffmpeg for Windows，解压到 vendor/ffmpeg/。

**只此一处**记录下载地址与哈希，CI 与本地构建都用这一份。校验不过 ⇒ 非零退出。
ffmpeg 二进制不许进 git（本项目硬规矩：大文件不进仓）。

**必须用 LGPL 版 ffmpeg**（例如 BtbN 的 `*-lgpl-shared`）——我们发的是闭源商业软件，
GPL 版会把整个产品拖进 GPL。这条不是技术偏好，是法律边界。
"""

import hashlib
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

# **哈希与下载地址只此一处**（CI 与本地构建都用这一份）
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n6.1.1-win64-lgpl-shared-6.1.zip"
# 占位哈希（首次真实构建前需手动下载 zip、计算实际 SHA256、替换这里）
FFMPEG_SHA256 = "45eb9e600e7a3d8760cbe8a67817842ed9b78bec961febc1bb9b4663087250ca"

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "ffmpeg"

# 从 zip 里要提取的文件（相对于 zip 根目录）
# BtbN 构建的 zip 结构：ffmpeg-n{version}-win64-lgpl-shared-{version}/bin/{ffmpeg.exe, ffprobe.exe, ...}
#                       ffmpeg-n{version}-win64-lgpl-shared-{version}/LICENSE
EXTRACT_PATTERNS = {
    "bin/ffmpeg.exe": "ffmpeg.exe",
    "bin/ffprobe.exe": "ffprobe.exe",
    "LICENSE": "LICENSE.txt",
    "COPYING.LGPLv2.1": "COPYING.LGPLv2.1.txt",
    "COPYING.LGPLv3": "COPYING.LGPLv3.txt",
}


def sha256_file(path: Path) -> str:
    """计算文件的 SHA256 哈希（十六进制小写）。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    print(f"下载 LGPL 版 ffmpeg from {FFMPEG_URL}")

    # 下载到临时文件
    tmp_zip = Path(__file__).resolve().parent / "ffmpeg-tmp.zip"
    try:
        urllib.request.urlretrieve(FFMPEG_URL, tmp_zip)
    except Exception as e:
        print(f"[错误] 下载失败：{e}", file=sys.stderr)
        return 1

    # SHA256 校验
    print(f"校验 SHA256...")
    actual_hash = sha256_file(tmp_zip)
    if actual_hash != FFMPEG_SHA256:
        print(f"[错误] SHA256 不匹配！", file=sys.stderr)
        print(f"  预期：{FFMPEG_SHA256}", file=sys.stderr)
        print(f"  实际：{actual_hash}", file=sys.stderr)
        tmp_zip.unlink()
        return 1

    print(f"  校验通过：{actual_hash}")

    # 解压到 vendor/ffmpeg/
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)

    print(f"解压到 {VENDOR_DIR}")
    with zipfile.ZipFile(tmp_zip) as zf:
        # BtbN 构建的 zip 有一个顶层目录，需要找到它
        # 列出所有文件，找到顶层目录名
        all_names = zf.namelist()
        if not all_names:
            print(f"[错误] zip 文件为空", file=sys.stderr)
            tmp_zip.unlink()
            return 1

        # 顶层目录名（例如 "ffmpeg-n6.1.1-win64-lgpl-shared-6.1/"）
        top_dir = all_names[0].split("/")[0]

        for zip_rel, out_name in EXTRACT_PATTERNS.items():
            # zip 内路径 = 顶层目录 + 相对路径
            zip_path = f"{top_dir}/{zip_rel}"
            out_path = VENDOR_DIR / out_name

            try:
                with zf.open(zip_path) as src:
                    with out_path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                print(f"  提取：{zip_path} -> {out_path.name}")
            except KeyError:
                print(f"[警告] zip 内找不到 {zip_path}，跳过", file=sys.stderr)

    tmp_zip.unlink()
    print(f"完成。ffmpeg 已解压到 {VENDOR_DIR}")

    # 验证必需文件
    required = ["ffmpeg.exe", "ffprobe.exe", "LICENSE.txt"]
    missing = [f for f in required if not (VENDOR_DIR / f).exists()]
    if missing:
        print(f"[错误] 缺少必需文件：{missing}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""下载并校验 LGPL 版 ffmpeg for Windows，解压到 vendor/ffmpeg/。

**只此一处**记录下载地址与哈希，CI 与本地构建都用这一份。校验不过 ⇒ 非零退出。
ffmpeg 二进制不许进 git（本项目硬规矩：大文件不进仓）。

**必须用 LGPL 版 ffmpeg**（BtbN 的 `*-lgpl-shared`）——我们发的是闭源商业软件，
GPL 版会把整个产品拖进 GPL。这条不是技术偏好，是法律边界。

版本由架构师实测并钉死（autobuild-2026-09-13-14-50 / n8.1.2 / lgpl-shared）。
-shared 构建需要 7 个 DLL（avcodec / avfilter / avformat / swscale / avdevice / avutil / swresample），
缺一个客户机上就是「无法启动此程序，因为计算机中丢失 …dll」。
"""

import hashlib
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

# **哈希与下载地址只此一处**（CI 与本地构建都用这一份）
# 架构师实测：字节数 80114619，顶层目录 ffmpeg-n8.1.2-52-g5a03dfa0f6-win64-lgpl-shared-8.1
# autobuild-<日期时刻> 是不动的 tag（latest 不是），lgpl-shared 满足法律边界
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-13-14-50/ffmpeg-n8.1.2-52-g5a03dfa0f6-win64-lgpl-shared-8.1.zip"
FFMPEG_SHA256 = "8bb18e29b002851b5c2cc765e575162ee1636263f7d900f79f62409d106138c9"
FFMPEG_BYTES = 80114619  # 长度校验比哈希失败可读得多

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "ffmpeg"


def sha256_file(path: Path) -> str:
    """计算文件的 SHA256 哈希（十六进制小写）。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    # Windows 编码归一化（packaging 不许 import depressionplex，所以内联三行）
    for stream in (sys.stdout, sys.stderr):
        fn = getattr(stream, "reconfigure", None)
        if fn is not None:
            fn(encoding="utf-8")

    print(f"下载 LGPL 版 ffmpeg from {FFMPEG_URL}")

    # 下载到临时文件
    tmp_zip = Path(__file__).resolve().parent / "ffmpeg-tmp.zip"
    try:
        urllib.request.urlretrieve(FFMPEG_URL, tmp_zip)
    except Exception as e:
        print(f"[错误] 下载失败：{e}", file=sys.stderr)
        return 1

    # 字节数校验（比哈希快，失败时可读性更好）
    actual_size = tmp_zip.stat().st_size
    if actual_size != FFMPEG_BYTES:
        print(f"[错误] 文件大小不匹配！", file=sys.stderr)
        print(f"  预期：{FFMPEG_BYTES} 字节", file=sys.stderr)
        print(f"  实际：{actual_size} 字节", file=sys.stderr)
        tmp_zip.unlink()
        return 1
    print(f"  大小校验通过：{actual_size} 字节")

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
        all_names = zf.namelist()
        if not all_names:
            print(f"[错误] zip 文件为空", file=sys.stderr)
            tmp_zip.unlink()
            return 1

        # 收集顶层目录（断言只有一个，不猜）
        top_dirs = {name.split("/")[0] for name in all_names if "/" in name}
        if len(top_dirs) != 1:
            print(f"[错误] zip 顶层目录不唯一：{top_dirs}", file=sys.stderr)
            tmp_zip.unlink()
            return 1
        top_dir = list(top_dirs)[0]
        print(f"  顶层目录：{top_dir}")

        # 提取规则：bin/ 下除 ffplay.exe 外的全部 + 根下的 LICENSE.txt
        # 不手写 DLL 名单——上游哪天把 avcodec-62 升成 -63，手写名单就又缺一个
        extracted_count = 0
        bin_prefix = f"{top_dir}/bin/"
        license_path = f"{top_dir}/LICENSE.txt"

        for member in all_names:
            out_name = None

            # bin/ 下的文件（跳过 ffplay.exe）
            if member.startswith(bin_prefix):
                basename = member[len(bin_prefix):]
                if basename and "/" not in basename:  # 只要直接子文件，不要子目录
                    if basename.lower() != "ffplay.exe":
                        out_name = basename

            # 根下的 LICENSE.txt
            elif member == license_path:
                out_name = "LICENSE.txt"

            if out_name:
                out_path = VENDOR_DIR / out_name
                try:
                    with zf.open(member) as src:
                        with out_path.open("wb") as dst:
                            shutil.copyfileobj(src, dst)
                    print(f"  提取：{member} -> {out_name}")
                    extracted_count += 1
                except KeyError as e:
                    # 任何成员取不到 ⇒ 立刻非零退出（H8）
                    print(f"[错误] zip 内找不到 {member}：{e}", file=sys.stderr)
                    tmp_zip.unlink()
                    return 1

    tmp_zip.unlink()
    print(f"完成。已提取 {extracted_count} 个文件到 {VENDOR_DIR}")

    # 验证必需文件（至少要有两个 exe 和许可）
    required = ["ffmpeg.exe", "ffprobe.exe", "LICENSE.txt"]
    missing = [f for f in required if not (VENDOR_DIR / f).exists()]
    if missing:
        print(f"[错误] 缺少必需文件：{missing}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

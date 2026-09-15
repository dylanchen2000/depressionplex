#!/usr/bin/env python3
"""下载并校验随包中文字体 Noto Sans SC，放到 vendor/fonts/。

**只此一处**记录下载地址与哈希，CI 与本地构建都用这一份。校验不过 ⇒ 非零退出。
字体二进制不许进 git（本项目硬规矩：大文件不进仓；`.gitignore` 里有 `vendor/`）。

**为什么必须随包带字体**（实测，run 34932358712）：
Qt 6.7.3 的 offscreen 平台插件在 windows-latest 上 `QFontDatabase.families()` 返回
**0 个** family——同一台机器上 `C:\\Windows\\Fonts\\msyh.ttc` 在位、换原生 windows 插件
能枚举到 154 个（其中 24 个能渲「中」）。也就是说「系统装了中文字体」不代表
我们的进程能用到它。而 `QFontDatabase.addApplicationFont()` 在同样条件下照样成功，
所以随包字体是唯一在所有平台插件下都可用的机制。

**为什么是 Noto Sans SC**：OFL 1.1 允许再分发（我们发的是闭源商业软件，
字体许可是法律边界，不是审美偏好）。许可正文一起下载、一起进安装包。
SubsetOTF 的单字重 8.3 MB，比 Sans2.004 的整套 CJK 小一个量级。

版本钉死在 tag `Sans2.004`（不是 `main`：滚动分支上的文件会变，哈希会失效）。
"""

import hashlib
import shutil
import sys
import urllib.request
from pathlib import Path

# **哈希与下载地址只此一处**（CI 与本地构建都用这一份）
# 架构师实测（2026-09-15）：8331336 字节，family 名 "Noto Sans SC"，
# cmap 里有 U+4E2D「中」。
_BASE = "https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004"

FONT_URL = f"{_BASE}/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf"
FONT_SHA256 = "faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9"
FONT_BYTES = 8331336  # 长度校验比哈希失败可读得多
FONT_FILENAME = "NotoSansSC-Regular.otf"

# OFL 1.1 正文。再分发必须带许可，这一条是法律边界。
LICENSE_URL = f"{_BASE}/LICENSE"
LICENSE_SHA256 = "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2"
LICENSE_BYTES = 4301
LICENSE_FILENAME = "LICENSE-NotoSansSC.txt"

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "fonts"


def sha256_file(path: Path) -> str:
    """计算文件的 SHA256 哈希（十六进制小写）。

    与 fetch_ffmpeg.py 里那份同形。**故意不跨脚本 import**：packaging/ 下的脚本
    既可能被 `python3 packaging/x.py` 调（sys.path[0] 是 packaging/），也可能被
    `python3 -m packaging.x` 调（sys.path[0] 是仓根），两种调法下
    `import fetch_ffmpeg` 行为不同——为了六行 stdlib 包装引入一处调法依赖不值得。
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_one(url: str, dest: Path, expect_bytes: int, expect_sha256: str) -> bool:
    """下载一个文件到 dest，先查长度再查哈希；任一不过 ⇒ 删临时文件并返回 False。

    已在位且哈希正确 ⇒ 不重新下载（CI 缓存命中时直接过）。
    """
    if dest.is_file() and dest.stat().st_size == expect_bytes:
        if sha256_file(dest) == expect_sha256:
            print(f"  已在位，跳过下载：{dest.name}")
            return True

    print(f"下载 {url}")
    tmp = dest.parent / f"{dest.name}.part"
    try:
        urllib.request.urlretrieve(url, tmp)
    except Exception as e:
        print(f"[错误] 下载失败：{e}", file=sys.stderr)
        return False

    actual_size = tmp.stat().st_size
    if actual_size != expect_bytes:
        print("[错误] 文件大小不匹配！", file=sys.stderr)
        print(f"  预期：{expect_bytes} 字节", file=sys.stderr)
        print(f"  实际：{actual_size} 字节", file=sys.stderr)
        tmp.unlink()
        return False
    print(f"  大小校验通过：{actual_size} 字节")

    actual_hash = sha256_file(tmp)
    if actual_hash != expect_sha256:
        print("[错误] SHA256 不匹配！", file=sys.stderr)
        print(f"  预期：{expect_sha256}", file=sys.stderr)
        print(f"  实际：{actual_hash}", file=sys.stderr)
        tmp.unlink()
        return False
    print(f"  校验通过：{actual_hash}")

    shutil.move(str(tmp), str(dest))
    return True


def main() -> int:
    # Windows 编码归一化（packaging 不许 import depressionplex，所以内联三行）
    for stream in (sys.stdout, sys.stderr):
        fn = getattr(stream, "reconfigure", None)
        if fn is not None:
            fn(encoding="utf-8")

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)

    ok = fetch_one(FONT_URL, VENDOR_DIR / FONT_FILENAME, FONT_BYTES, FONT_SHA256)
    if not ok:
        return 1
    # 字体过了才拉许可：许可失败也一样是失败退出，但先失败的那个报错更有用
    ok = fetch_one(LICENSE_URL, VENDOR_DIR / LICENSE_FILENAME,
                   LICENSE_BYTES, LICENSE_SHA256)
    if not ok:
        return 1

    missing = [n for n in (FONT_FILENAME, LICENSE_FILENAME)
               if not (VENDOR_DIR / n).is_file()]
    if missing:
        print(f"[错误] 缺少必需文件：{missing}", file=sys.stderr)
        return 1

    print(f"完成。字体与许可已在 {VENDOR_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

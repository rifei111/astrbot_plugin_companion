"""打发布用的插件 zip。

要点只有一个：**路径分隔符必须是 "/"**。Windows 上 `Compress-Archive` 会把
条目写成 `astrbot_plugin_companion\\main.py`，在 Linux/Docker 上用 unzip 或
Python zipfile 解压会得到一个名字里带反斜杠的扁平文件，插件目录结构就废了，
AstrBot 加载直接失败。而 AstrBot 绝大多数部署在 Linux 上，所以发布包必须在
源头就写对。

`zipfile.ZipFile.write()` 内部会把 os.sep 换成 "/"，用它比调外部命令可靠。
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "astrbot_plugin_companion"
DIST = ROOT / "dist"

EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".ruff_cache",
    ".pytest_cache",
    ".venv",
    "data",
    "dist",
    "tools",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db"}


def read_version() -> str:
    text = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
    match = re.search(r"^version:\s*['\"]?([\w.\-+]+)", text, re.MULTILINE)
    if not match:
        raise SystemExit("metadata.yaml 里没找到 version")
    return match.group(1)


def iter_files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() in EXCLUDE_SUFFIXES or path.name in EXCLUDE_NAMES:
            continue
        yield path, rel


def build(version: str) -> Path:
    DIST.mkdir(exist_ok=True)
    target = DIST / f"{PACKAGE}-{version}.zip"
    if target.exists():
        target.unlink()
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, rel in iter_files():
            # arcname 用 posix 风格，包在 AstrBot 里展开成一个顶层目录。
            arcname = f"{PACKAGE}/{rel.as_posix()}"
            zf.write(path, arcname)
            count += 1
    return target if count else _fail("没有可打包的文件")


def _fail(message: str) -> Path:
    raise SystemExit(message)


def verify(target: Path) -> None:
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    bad = [name for name in names if "\\" in name]
    if bad:
        raise SystemExit(f"条目里还有反斜杠：{bad[:3]}")
    if f"{PACKAGE}/metadata.yaml" not in names:
        raise SystemExit(f"zip 里缺少 {PACKAGE}/metadata.yaml")
    if f"{PACKAGE}/main.py" not in names:
        raise SystemExit(f"zip 里缺少 {PACKAGE}/main.py")


def main() -> None:
    version = read_version()
    target = build(version)
    verify(target)
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    print(f"{target}")
    print(f"  version={version} files={len(names)} bytes={target.stat().st_size}")
    for name in names:
        print(f"  {name}")


if __name__ == "__main__":
    sys.exit(main())

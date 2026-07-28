"""构建 Windows 发行版。

    python packaging/build.py              # 打包 + 自检
    python packaging/build.py --installer  # 再出安装程序（需要 Inno Setup）
    python packaging/build.py --portable   # 再出便携版 zip

必须在 Windows 上运行——PyInstaller 不能交叉编译。

产物在 packaging/output/：
    Marginalia/                       onedir 目录
    Marginalia-x.y.z-Setup.exe        安装程序
    Marginalia-x.y.z-portable.zip     便携版
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
BUILD = PACKAGING / "build"
OUTPUT = PACKAGING / "output"
APP_NAME = "Marginalia"

#: Inno Setup 编译器的常见位置
ISCC_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def read_version() -> str:
    """版本号只有一个来源：marginalia/__init__.py。"""
    text = (ROOT / "marginalia" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("在 marginalia/__init__.py 里找不到 __version__")
    return match.group(1)


def write_version_info(version: str) -> None:
    """生成 Windows 的版本资源，右键属性里能看到的那些字段。"""
    parts = [int(p) for p in re.findall(r"\d+", version)][:4]
    parts += [0] * (4 - len(parts))
    quad = ", ".join(str(p) for p in parts)

    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "version_info.txt").write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({quad}), prodvers=({quad}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', 'Marginalia'),
        StringStruct('FileDescription', '本地 PDF 阅读与笔记工具'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'Marginalia'),
        StringStruct('LegalCopyright', 'MIT License'),
        StringStruct('OriginalFilename', 'Marginalia.exe'),
        StringStruct('ProductName', 'Marginalia'),
        StringStruct('ProductVersion', '{version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""",
        encoding="utf-8",
    )


def run_pyinstaller() -> Path:
    print("==> PyInstaller 打包")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(OUTPUT),
            "--workpath",
            str(BUILD / "work"),
            # 注意：给了 .spec 就不能再传 --specpath 之类的 makespec 选项，
            # 那些设置只能写在 spec 文件里
            str(PACKAGING / "marginalia.spec"),
        ],
        check=True,
        cwd=ROOT,
    )
    app_dir = OUTPUT / APP_NAME
    if not app_dir.is_dir():
        raise SystemExit(f"打包产物不在预期位置：{app_dir}")
    return app_dir


def run_selftest(app_dir: Path) -> None:
    """打完包必须验一遍。

    Qt 插件缺失、原生库没带上这类问题在源码运行时完全正常，只有跑打好的包才会暴露。
    """
    print("==> 自检打包产物")
    exe = app_dir / f"{APP_NAME}.exe"
    if not exe.exists():  # 非 Windows 上试跑时的兜底
        exe = app_dir / APP_NAME
    result = subprocess.run(
        [str(exe), "--selftest"], capture_output=True, text=True, timeout=300
    )
    print(result.stdout or result.stderr)
    if result.returncode != 0:
        raise SystemExit("自检未通过，产物不可用")


def report_size(app_dir: Path) -> None:
    total = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file())
    print(f"==> 打包体积 {total / 1024 / 1024:.0f} MB")


def build_portable(app_dir: Path, version: str) -> Path:
    """便携版：自带空的 data\\ 文件夹，解压即处于便携模式。"""
    print("==> 打包便携版")
    target = OUTPUT / f"{APP_NAME}-{version}-portable.zip"
    target.unlink(missing_ok=True)

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                archive.write(path, Path(APP_NAME) / path.relative_to(app_dir))
        # data\ 存在即启用便携模式，放个说明文件把目录带进 zip
        archive.writestr(
            f"{APP_NAME}/data/README.txt",
            "这个文件夹存在时，Marginalia 会把书库和笔记存在这里（便携模式）。\r\n"
            "删掉这个文件夹，程序就会改用「文档\\Marginalia」。\r\n",
        )
    print(f"    {target.name}  {target.stat().st_size / 1024 / 1024:.0f} MB")
    return target


#: 属于 OCR 组件的文件（相对 _internal 的顶层名字前缀）
OCR_PREFIXES = ("rapidocr_onnxruntime", "onnxruntime")


def split_ocr_component(app_dir: Path) -> Path | None:
    """把 OCR 相关文件挪到单独的目录，安装程序才能把它做成可选组件。

    做成「搬到另一个目录树」而不是在 .iss 里写 glob 排除：排除模式一旦跟不上
    依赖的目录结构变化，会静默地把文件打进主程序（体积白涨）或漏掉（运行期才炸）。
    这里搬完之后哪边有什么是看得见的。
    """
    internal = app_dir / "_internal"
    if not internal.is_dir():
        internal = app_dir  # PyInstaller 5 及更早没有 _internal 这层

    staging = OUTPUT / "ocr-component"
    shutil.rmtree(staging, ignore_errors=True)

    moved: list[str] = []
    target_internal = staging / internal.relative_to(app_dir) if internal != app_dir else staging
    for entry in sorted(internal.iterdir()):
        if not entry.name.startswith(OCR_PREFIXES):
            continue
        target_internal.mkdir(parents=True, exist_ok=True)
        shutil.move(str(entry), str(target_internal / entry.name))
        moved.append(entry.name)

    if not moved:
        print("==> 本次构建不含 OCR（未安装 rapidocr-onnxruntime）")
        return None

    size = sum(f.stat().st_size for f in staging.rglob("*") if f.is_file())
    print(f"==> OCR 组件已拆出 {len(moved)} 项，{size / 1024 / 1024:.0f} MB")
    return staging


def find_iscc() -> Path | None:
    for candidate in ISCC_CANDIDATES:
        if candidate.exists():
            return candidate
    found = shutil.which("iscc")
    return Path(found) if found else None


def build_installer(version: str) -> Path | None:
    print("==> 生成安装程序")
    iscc = find_iscc()
    if iscc is None:
        print("    跳过：找不到 Inno Setup（ISCC.exe）")
        print("    从 https://jrsoftware.org/isdl.php 安装后重试")
        return None

    (BUILD / "version.iss").write_text(
        f'#define AppVersion "{version}"\n', encoding="utf-8"
    )
    subprocess.run([str(iscc), str(PACKAGING / "marginalia.iss")], check=True, cwd=ROOT)

    target = OUTPUT / f"{APP_NAME}-{version}-Setup.exe"
    if target.exists():
        print(f"    {target.name}  {target.stat().st_size / 1024 / 1024:.0f} MB")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 Marginalia 的 Windows 发行版")
    parser.add_argument("--installer", action="store_true", help="生成安装程序")
    parser.add_argument("--portable", action="store_true", help="生成便携版 zip")
    parser.add_argument("--all", action="store_true", help="两个都生成")
    parser.add_argument("--skip-selftest", action="store_true", help="跳过自检（不建议）")
    args = parser.parse_args()

    if os.name != "nt":
        print("警告：PyInstaller 不能交叉编译，非 Windows 上只能做冒烟验证\n")

    version = read_version()
    print(f"Marginalia {version}\n")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_version_info(version)
    app_dir = run_pyinstaller()
    report_size(app_dir)

    if not args.skip_selftest:
        run_selftest(app_dir)

    # 顺序有讲究：便携版要的是「什么都有」的完整目录，所以先打 zip 再拆 OCR
    if args.portable or args.all:
        build_portable(app_dir, version)
    if args.installer or args.all:
        split_ocr_component(app_dir)
        build_installer(version)

    print(f"\n完成。产物在 {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

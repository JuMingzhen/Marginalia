# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

**onedir 而不是 onefile。** onefile 每次启动都要把两百多 MB 解压到临时目录，
冷启动好几秒；阅读器是天天开的东西，这个代价不能接受。装完之后目录结构反正
被安装程序藏起来了，用户只看得到快捷方式。

**要显式排除一大堆 Qt 模块。** PySide6 的 hook 会尽量多带，而 QtWebEngine 一家
就有一百多 MB，我们一行都用不到。排除之后体积大约减半。每次加新功能都该回来
看一眼这份清单还对不对——排错了会在运行期才炸，所以打完包务必跑 --selftest。

由 packaging/build.py 调用，不要直接 pyinstaller marginalia.spec：
版本信息文件是构建时生成的。
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))

#: 一行都用不到、但 PySide6 的 hook 会顺手打进来的东西
EXCLUDED_QT = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtUiTools",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",  # 我们用 PyMuPDF，不用 Qt 自带的 PDF 模块
    "PySide6.QtPdfWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
]

EXCLUDED_OTHER = [
    "tkinter",
    "matplotlib",
    "scipy",
    "pandas",
    "IPython",
    "pytest",
    "setuptools",
    "pip",
]

#: OCR 是可选组件。装了就打进去，没装也能出包（程序会提示未安装）
hidden = []
try:
    import rapidocr_onnxruntime  # noqa: F401

    hidden += ["rapidocr_onnxruntime", "onnxruntime"]
    WITH_OCR = True
except ImportError:
    WITH_OCR = False

datas = [
    (str(ROOT / "marginalia" / "resources"), "marginalia/resources"),
]
if WITH_OCR:
    import rapidocr_onnxruntime as _rapid

    # 模型文件和配置不是 Python 模块，hook 抓不到，得手动带
    datas.append((str(Path(_rapid.__file__).parent), "rapidocr_onnxruntime"))


a = Analysis(
    [str(ROOT / "marginalia" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDED_QT + EXCLUDED_OTHER,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Marginalia",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX 压缩会让杀毒软件误报，省下来的体积不值这个麻烦
    console=False,  # 窗口程序，不弹黑框
    disable_windowed_traceback=False,
    icon=str(ROOT / "marginalia" / "resources" / "icon.ico"),
    version=str(ROOT / "packaging" / "build" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Marginalia",
)

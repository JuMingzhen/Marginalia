# 开发

## 环境

代码是纯 Python，跨平台。开发和测试在哪都行（GUI 测试跑 Qt 的 offscreen 后端，
不需要显示服务）；**打包必须在 Windows 上做**——PyInstaller 不能交叉编译。

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"        # Windows: .venv\Scripts\pip

.venv/bin/python -m pytest tests/         # 测试
.venv/bin/ruff check marginalia tests     # 检查
.venv/bin/ruff format marginalia          # 格式化
.venv/bin/python -m marginalia            # 直接运行
```

装本地 OCR（读扫描版需要）：

```bash
.venv/bin/pip install -e ".[ocr]"
```

## 在 WSL 里开发

WSLg 能显示 GUI，但字体渲染和高 DPI 缩放不如原生，长时间试读会别扭。建议：
**逻辑改动在 WSL 里跑测试，观感相关的改动到 Windows 上验。**

Windows 侧跑源码（不打包）：

```powershell
py -3.13 -m venv $env:USERPROFILE\.venvs\marginalia
& "$env:USERPROFILE\.venvs\marginalia\Scripts\pip" install -e "\\wsl.localhost\Ubuntu\home\jmz\dev\Marginalia[ocr]"
& "$env:USERPROFILE\.venvs\marginalia\Scripts\python" -m marginalia
```

虚拟环境刻意放在 Windows 本地盘而不是仓库里——仓库在 WSL 文件系统上，
把上百 MB 的 PySide6 装到 UNC 路径又慢又容易出问题。

## 目录

```
marginalia/
  app/        入口、配置、数据目录解析
  core/       PDF 文档、渲染线程、词框索引、配色
  store/      书库、笔记、进度、截图（全是文件，没有数据库）
  services/   OCR（可插拔后端）、LLM
  ui/         主窗、画布、侧栏、编辑卡
packaging/    PyInstaller spec、Inno Setup 脚本、构建脚本
tests/        pytest
docs/         设计文档
```

三个工程重心：

- `core/render.py` — 渲染线程与位图缓存。PyMuPDF 的 `Document` 非线程安全，
  渲染线程持有独立句柄；世代计数器负责丢弃快速滚动时积压的过期请求。
- `core/textmap.py` — 词框索引与拖选，全项目唯一有算法含量的地方。
- `ui/page_view.py` — 虚拟化画布，只渲染视口附近的页。

## 自检

打包后用自检模式确认冻结的程序真的能跑：

```
Marginalia.exe --selftest
```

它会开一本内置的测试 PDF、渲染一页、建一条笔记、读回来、退出，
任何一步失败都返回非零。CI 靠它把关。

## 测试约定

- 所有涉及数据目录的测试用 `MARGINALIA_DATA_DIR` 指到 `tmp_path`，互不干扰
- GUI 测试用 `conftest.py` 里的 `qapp` / `pump` fixture；`pump(秒)` 转事件循环等后台线程
- 断言写「为什么」而不只是「是什么」——docstring 里说明这条性质为什么重要

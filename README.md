# Reader

本地 PDF 阅读与笔记桌面应用。纯离线，笔记以 JSONL 存在本地文件里，原 PDF 永不修改。

设计文档见 [DESIGN.md](DESIGN.md)。

## 运行环境

代码在 WSL 里编辑，**程序在 Windows 侧运行**（原生字体渲染 / HiDPI / 文件关联）。

### 首次安装

在资源管理器里打开 `\\wsl.localhost\Ubuntu\home\jmz\dev\Reader\scripts\`，**双击 `setup.cmd`**。

会在 `%USERPROFILE%\.venvs\reader` 建一个虚拟环境并装好依赖。虚拟环境刻意不放在仓库里——
仓库在 WSL 文件系统上，把 120MB 的 PySide6 装到 UNC 路径上又慢又容易出问题。

要读扫描版（无文字层的 PDF），在 PowerShell 里带 `-Ocr` 参数装本地 OCR：

```powershell
& "\\wsl.localhost\Ubuntu\home\jmz\dev\Reader\scripts\setup.cmd" -Ocr
```

没装也能用，只是框选出来的原文得自己录。

### 启动

双击 `scripts\run.cmd`，或者把 PDF 拖到它上面直接打开那本书。

> **为什么是 `.cmd` 而不是直接跑 `.ps1`**：仓库在 WSL 文件系统上，Windows 通过
> `\\wsl.localhost\` 访问时算「远程」区域，默认的 RemoteSigned 执行策略会拒绝运行
> 那里的未签名 PowerShell 脚本。`.cmd` 不受执行策略约束，由它转手调用即可。
> 同理，两个 `.ps1` 存成带 BOM 的 UTF-8——PowerShell 5.1 否则会按 GBK 读，中文全是乱码。

`run.cmd -Quiet` 用 `pythonw.exe` 启动，不带控制台窗口。

## 用法速记

| 操作 | 快捷键 |
|---|---|
| 打开 / 跳页 / 搜索目录 | `Ctrl+O` / `Ctrl+G` / `Ctrl+B` |
| 缩放 | `Ctrl+滚轮`、`Ctrl+±`、`Ctrl+0`、`Ctrl+1` 适配宽度、`Ctrl+2` 适配整页 |
| 配色（原色 / 纸色 / 夜间） | `Ctrl+T` |
| 高亮选中文字 / 写批注 | `H` / `N` |
| 框选模式（扫描版） | `Ctrl+R`，或随时按住 `Alt` 拖 |
| 笔记侧栏 | `Ctrl+Shift+B` |

划词后就地会浮出小工具条：点颜色直接高亮，点「批注」展开编辑卡。
扫描版打开时自动进入框选模式——拖出矩形即按 300 DPI 截图并送去 OCR，
卡片立刻弹出，识别结果晚一两秒填进「原文」框，可以直接改。

## 数据

用户数据在 `%USERPROFILE%\.reader\`，可用环境变量 `READER_DATA_DIR` 覆盖。

```
.reader/
  config.json        配置
  library.jsonl      书库索引
  docs/<doc_id>/     每本书的笔记、进度、截图、OCR 缓存
```

## 开发

WSL 侧建一个开发环境跑测试和检查（GUI 测试用 Qt 的 offscreen 后端，不需要显示服务）：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest ruff

.venv/bin/python -m pytest tests/   # 测试
.venv/bin/ruff check reader/ tests/ # 检查
.venv/bin/ruff format reader/       # 格式化
```

分支约定：功能在 `feat/*` 分支上做，完成后合并回 `main`。

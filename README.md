# Reader

本地 PDF 阅读与笔记桌面应用。纯离线，笔记以 JSONL 存在本地文件里，原 PDF 永不修改。

设计文档见 [DESIGN.md](DESIGN.md)。

## 运行环境

代码在 WSL 里编辑，**程序在 Windows 侧运行**（原生字体渲染 / HiDPI / 文件关联）。

### 首次安装

在 Windows PowerShell 里执行：

```powershell
\\wsl.localhost\Ubuntu\home\jmz\dev\Reader\scripts\setup.ps1
```

会在 `%USERPROFILE%\.venvs\reader` 建一个虚拟环境并装好依赖。虚拟环境刻意不放在仓库里——
仓库在 WSL 文件系统上，把 120MB 的 PySide6 装到 UNC 路径上又慢又容易出问题。

### 启动

```powershell
\\wsl.localhost\Ubuntu\home\jmz\dev\Reader\scripts\run.ps1
```

带 `-Quiet` 用 `pythonw.exe` 启动（无控制台窗口）。

## 数据

用户数据在 `%USERPROFILE%\.reader\`，可用环境变量 `READER_DATA_DIR` 覆盖。

```
.reader/
  config.json        配置
  library.jsonl      书库索引
  docs/<doc_id>/     每本书的笔记、进度、截图、OCR 缓存
```

## 开发

```bash
ruff check reader/     # 代码检查
ruff format reader/    # 格式化
```

分支约定：功能在 `feat/*` 分支上做，完成后合并回 `main`。

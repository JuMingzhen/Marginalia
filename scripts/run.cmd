@echo off
rem 启动 Marginalia。可以直接在资源管理器里双击，也可以把 PDF 拖到它上面打开。
rem 见 setup.cmd 里关于执行策略的说明。

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*

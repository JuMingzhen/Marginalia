@echo off
rem 安装 Marginalia 的运行环境。可以直接在资源管理器里双击。
rem
rem 为什么要有这个 .cmd：仓库在 WSL 文件系统上，Windows 通过 \\wsl.localhost\ 访问，
rem 属于「远程」区域。默认的 RemoteSigned 执行策略会拒绝运行那里的未签名 .ps1。
rem 批处理文件不受执行策略约束，由它转手调用 PowerShell 并绕过该限制。

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
if errorlevel 1 (
    echo.
    echo 安装失败，请查看上面的错误信息。
)
pause

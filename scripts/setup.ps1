<#
.SYNOPSIS
    在 Windows 侧创建虚拟环境并安装 Marginalia 的依赖。

.DESCRIPTION
    虚拟环境建在 %USERPROFILE%\.venvs\marginalia，而不是仓库目录里。
    仓库位于 WSL 文件系统（UNC 路径），把上百 MB 的包装到 UNC 上既慢又容易
    出现 Scripts\*.exe 启动器失效的问题。

.PARAMETER Ocr
    额外安装本地 OCR 依赖（rapidocr-onnxruntime，约 50MB）。

.PARAMETER Ai
    额外安装 LLM 依赖（anthropic）。
#>
[CmdletBinding()]
param(
    [switch]$Ocr,
    [switch]$Ai
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $env:USERPROFILE ".venvs\marginalia"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "仓库    : $RepoRoot"
Write-Host "虚拟环境: $VenvDir"
Write-Host ""

if (-not (Test-Path $VenvPython)) {
    Write-Host "==> 创建虚拟环境 (Python 3.13)"
    & py -3.13 -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败" }
} else {
    Write-Host "==> 虚拟环境已存在，跳过创建"
}

Write-Host "==> 升级 pip"
& $VenvPython -m pip install --upgrade pip --quiet

Write-Host "==> 安装核心依赖（PySide6 较大，首次需要几分钟）"
& $VenvPython -m pip install -r (Join-Path $RepoRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "安装依赖失败" }

if ($Ocr) {
    Write-Host "==> 安装本地 OCR 依赖"
    & $VenvPython -m pip install "rapidocr-onnxruntime>=1.3"
}

if ($Ai) {
    Write-Host "==> 安装 LLM 依赖"
    & $VenvPython -m pip install "anthropic>=0.40"
}

Write-Host ""
Write-Host "完成。启动方式：" -ForegroundColor Green
Write-Host "  双击 $(Join-Path $PSScriptRoot 'run.cmd')"

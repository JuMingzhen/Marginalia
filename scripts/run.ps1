<#
.SYNOPSIS
    启动 Marginalia。

.DESCRIPTION
    用 %USERPROFILE%\.venvs\marginalia 里的解释器，以仓库根目录作为 PYTHONPATH 运行，
    不需要把项目 pip install 进虚拟环境——源码改了直接重启即可生效。

.PARAMETER Quiet
    用 pythonw.exe 启动，不带控制台窗口（日志将不可见）。

.PARAMETER Path
    启动时直接打开的 PDF 文件路径。
#>
[CmdletBinding()]
param(
    [switch]$Quiet,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Path
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $env:USERPROFILE ".venvs\marginalia"
$Exe = if ($Quiet) { "Scripts\pythonw.exe" } else { "Scripts\python.exe" }
$Python = Join-Path $VenvDir $Exe

if (-not (Test-Path $Python)) {
    throw "找不到虚拟环境。请先双击运行 $(Join-Path $PSScriptRoot 'setup.cmd')"
}

$env:PYTHONPATH = $RepoRoot
$env:PYTHONUTF8 = "1"

& $Python -m marginalia @Path

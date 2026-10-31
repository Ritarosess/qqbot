# ============================================================
# 建立到 NapCat WebUI 的 SSH 隧道（Windows 端执行）
# 这个窗口必须一直开着，关掉隧道就断
#
# 用法：.\deploy\tunnel.ps1 -Server 47.100.1.2
# 然后浏览器打开 http://127.0.0.1:6099/webui
# ============================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$User = "ecs-user",
    [int]$Port = 22,
    [string]$KeyPath = (Join-Path $PSScriptRoot "keys\aliyun_ed25519"),
    [int]$LocalPort = 6099
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $KeyPath)) { throw "私钥不存在：$KeyPath" }
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) { throw "系统未安装 OpenSSH 客户端" }

Write-Host "==> 隧道已建立，浏览器访问 http://127.0.0.1:$LocalPort/webui" -ForegroundColor Green
Write-Host "    按 Ctrl+C 断开（断开后 WebUI 立即不可访问）" -ForegroundColor Yellow

& ssh -i $KeyPath -p $Port -N `
    -o StrictHostKeyChecking=accept-new `
    -o ConnectTimeout=15 `
    -o ServerAliveInterval=30 `
    -o ExitOnForwardFailure=yes `
    -L "${LocalPort}:127.0.0.1:6099" `
    "$User@$Server"

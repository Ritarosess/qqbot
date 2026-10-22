# ============================================================
# 打开到阿里云 ECS 的 SSH 隧道（本机 6185 -> AstrBot WebUI）
#
# 用法（项目根目录执行，窗口保持不关）：
#   .\deploy\tunnel-astrbot.ps1 -Server 47.98.218.89
#   .\deploy\tunnel-astrbot.ps1 -Server 47.98.218.89 -LocalPort 16185
#
# 原理：ssh -N -L 6185:127.0.0.1:6185
# 关闭：Ctrl+C，或在窗口按 Ctrl+Break
# ============================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Server,

    [string]$User = "ecs-user",
    [int]$Port = 22,
    [int]$LocalPort = 6185,
    [string]$KeyPath = (Join-Path $PSScriptRoot "keys\aliyun_ed25519")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $KeyPath)) {
    throw "私钥不存在：$KeyPath"
}
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    throw "系统未安装 OpenSSH 客户端（设置 → 应用 → 可选功能 → OpenSSH 客户端）"
}

# 端口被占用时提前提示
$busy = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    throw "本机端口 $LocalPort 已被占用，换个端口：-LocalPort 16185"
}

Write-Host ""
Write-Host "隧道已建立，浏览器打开： http://127.0.0.1:$LocalPort" -ForegroundColor Green
Write-Host "窗口不要关；关闭隧道按 Ctrl+C" -ForegroundColor Yellow
Write-Host ""

& ssh -i $KeyPath -p $Port -N `
    -o StrictHostKeyChecking=accept-new `
    -o ConnectTimeout=15 `
    -o ServerAliveInterval=30 `
    -L "$($LocalPort):127.0.0.1:6185" `
    "$User@$Server"

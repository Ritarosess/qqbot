# ============================================================
# 打开到阿里云 ECS 的 SSH 隧道（本机 8610 -> 设置面板）
#
# 用法（项目根目录执行，窗口保持不关）：
#   .\deploy\tunnel-dashboard.ps1 -Server <TAILSCALE_IP>
#   .\deploy\tunnel-dashboard.ps1 -Server <TAILSCALE_IP> -LocalPort 18610
#
# 打开后浏览器访问：http://127.0.0.1:8610/
# 首次会要求输入 token（服务器上 /opt/qqbot-dashboard/token）
# 关闭：Ctrl+C，或关闭窗口
# ============================================================
[CmdletBinding()]
param(
    # 默认走 Tailscale 内网地址；连不上时再显式传 -Server <TAILSCALE_IP>
    [string]$Server = $env:QQBOT_SERVER,

    [string]$User = "ecs-user",
    [int]$Port = 22,
    [int]$LocalPort = 8610,
    [string]$KeyPath = (Join-Path $PSScriptRoot "keys\aliyun_ed25519")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $KeyPath)) {
    throw "私钥不存在：$KeyPath"
}
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    throw "系统未安装 OpenSSH 客户端（设置 → 应用 → 可选功能 → OpenSSH 客户端）"
}

$KnownHosts = Join-Path $PSScriptRoot ".ssh_known_hosts_tmp"

Write-Host "==> 建立隧道：http://127.0.0.1:$LocalPort/  ->  $Server`:$Port (远程 127.0.0.1:8610)"
Write-Host "    浏览器打开 http://127.0.0.1:$LocalPort/ ，token 见服务器 /opt/qqbot-dashboard/token"
Write-Host "    保持本窗口不关；Ctrl+C 结束"

& ssh -N `
    -p $Port `
    -i $KeyPath `
    -o "StrictHostKeyChecking=accept-new" `
    -o "UserKnownHostsFile=$KnownHosts" `
    -o "ExitOnForwardFailure=yes" `
    -o "ServerAliveInterval=30" `
    -L "$($LocalPort):127.0.0.1:8610" `
    "$User@$Server"

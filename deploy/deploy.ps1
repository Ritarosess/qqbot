# ============================================================
# QQBOT 部署到阿里云 ECS（Windows 端执行）
#
# 用法：
#   .\deploy\deploy.ps1 -Server 47.100.1.2                      # 阿里云 Ubuntu 默认用户 ecs-user
#   .\deploy\deploy.ps1 -Server 47.100.1.2 -User root           # 其他系统用 root
#   .\deploy\deploy.ps1 -Server 47.100.1.2 -SkipInstall         # 只上传不安装
#   .\deploy\deploy.ps1 -Server 47.100.1.2 -Restart             # 上传安装并重启机器人
#
# 前提：已在阿里云控制台把 deploy\keys\aliyun_ed25519.pub 绑定到实例
# 说明：非 root 用户需要免密 sudo（阿里云 ecs-user 默认满足）
# ============================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Server,

    [string]$User = "ecs-user",
    [int]$Port = 22,
    [string]$KeyPath = (Join-Path $PSScriptRoot "keys\aliyun_ed25519"),
    [string]$RemoteDir = "/opt/qqbot",
    [switch]$SkipInstall,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$Archive = Join-Path $env:TEMP "qqbot-deploy.tar.gz"
$RemoteArchive = "/tmp/qqbot-deploy.tar.gz"
$Sudo = if ($User -eq "root") { "" } else { "sudo -n " }

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "[警告] $msg" -ForegroundColor Yellow }

# ---------- 前置检查 ----------
if (-not (Test-Path $KeyPath)) {
    throw "私钥不存在：$KeyPath`n请先生成密钥（见 deploy\README.md）"
}
if (-not (Test-Path (Join-Path $ProjectRoot "bot.py"))) {
    throw "项目目录不正确：$ProjectRoot"
}
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    throw "系统未安装 OpenSSH 客户端"
}
if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
    throw "系统未安装 tar（Windows 10 1803+ 自带）"
}

# Windows OpenSSH 要求私钥权限收紧，否则会拒绝使用
# 某些受限环境（如沙箱）没有 SeSecurityPrivilege，此时跳过即可
try {
    $acl = Get-Acl $KeyPath
    $acl.SetAccessRuleProtection($true, $false)
    $acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) | Out-Null }
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        "$env:USERNAME", "Read", "Allow")
    $acl.AddAccessRule($rule)
    Set-Acl -Path $KeyPath -AclObject $acl
} catch {
    Write-Warn2 "收紧私钥权限失败（$($_.Exception.Message)），若 SSH 报 permissions too open，请手动执行："
    Write-Warn2 "  icacls `"$KeyPath`" /inheritance:r /grant:r `"$($env:USERNAME):(R)`""
}

$sshArgs = @(
    "-i", $KeyPath,
    "-p", "$Port",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=15",
    "-o", "ServerAliveInterval=30"
)
# scp 的端口参数是大写 -P，不能用 ssh 的 -p
$scpArgs = @(
    "-i", $KeyPath,
    "-P", "$Port",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=15"
)
$Target = "$User@$Server"

# ---------- 1. 连通性 ----------
Write-Step "测试 SSH 连接：$Target : $Port"
& ssh @sshArgs $Target "echo CONNECT_OK; (. /etc/os-release 2>/dev/null && echo OS=`${PRETTY_NAME}) || uname -a"
if ($LASTEXITCODE -ne 0) {
    throw "SSH 连接失败。请检查：公网 IP、安全组是否放行 $Port、公钥是否已绑定、用户名是否正确"
}

if ($User -ne "root") {
    Write-Step "检查免密 sudo"
    & ssh @sshArgs $Target "sudo -n true"
    if ($LASTEXITCODE -ne 0) {
        throw "用户 $User 无法免密 sudo。请为它配置 NOPASSWD，或改用 -User root"
    }
}

# ---------- 2. 打包 ----------
Write-Step "打包项目（排除 .venv / .git / 私钥 / 缓存）"
if (Test-Path $Archive) { Remove-Item $Archive -Force }
$parent = Split-Path $ProjectRoot -Leaf
& tar -czf $Archive `
    --exclude "$parent/.venv" `
    --exclude "$parent/__pycache__" `
    --exclude "$parent/.git" `
    --exclude "$parent/.idea" `
    --exclude "$parent/.vscode" `
    --exclude "$parent/deploy/keys" `
    --exclude "$parent/deploy/wip-backup" `
    --exclude "$parent/声音素材" `
    --exclude "$parent/*.log" `
    --exclude "$parent/*.wav" `
    --exclude "$parent/*.mp3" `
    --exclude "$parent/emojis.json" `
    --exclude "$parent/whitelist.json" `
    --exclude "$parent/whitelist.json.tmp" `
    -C (Split-Path $ProjectRoot -Parent) $parent
if ($LASTEXITCODE -ne 0) { throw "打包失败" }
$size = "{0:N2} MB" -f ((Get-Item $Archive).Length / 1MB)
Write-Step "包大小：$size"

# ---------- 3. 上传 ----------
Write-Step "上传到服务器 $RemoteArchive"
& scp @scpArgs $Archive "${Target}:$RemoteArchive"
if ($LASTEXITCODE -ne 0) { throw "上传失败" }

# ---------- 4. 解压 + 安装 ----------
Write-Step "解压到 $RemoteDir"
$remoteCmd = @"
set -e
${Sudo}mkdir -p '$RemoteDir'
${Sudo}tar -xzf '$RemoteArchive' -C '$RemoteDir' --strip-components=1 --overwrite
${Sudo}chmod +x '$RemoteDir/deploy/install.sh'
echo EXTRACT_OK
"@
& ssh @sshArgs $Target $remoteCmd
if ($LASTEXITCODE -ne 0) { throw "远端解压失败" }

if (-not $SkipInstall) {
    Write-Step "执行服务器端安装脚本（约 1-3 分钟）"
    & ssh @sshArgs $Target "${Sudo}bash '$RemoteDir/deploy/install.sh'"
    if ($LASTEXITCODE -ne 0) { throw "安装脚本执行失败，请查看上面的输出" }
} else {
    Write-Warn2 "已跳过安装（-SkipInstall）"
}

if ($Restart) {
    Write-Step "重启机器人服务"
    & ssh @sshArgs $Target "${Sudo}systemctl restart qqbot && ${Sudo}systemctl --no-pager --lines=15 status qqbot"
}

Write-Step "完成"
Write-Host @"

后续步骤：
 1. 开 SSH 隧道访问 NapCat WebUI（另开一个窗口，保持不关）：
      .\deploy\tunnel.ps1 -Server $Server
 2. 浏览器打开 http://127.0.0.1:6099/webui ，输入服务器上显示的 WebUI Token
    （查看命令：ssh -i "$KeyPath" $Target "grep WEBUI_TOKEN /opt/napcat/.env"）
 3. 用手机 QQ 扫码登录机器人账号
 4. 启动机器人：ssh -i "$KeyPath" $Target "${Sudo}systemctl start qqbot"
 5. 看日志：    ssh -i "$KeyPath" $Target "${Sudo}journalctl -u qqbot -f"
"@ -ForegroundColor Cyan

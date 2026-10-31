@echo off
setlocal
rem ============================================================
rem 双击运行：本机隧道到 AstrBot WebUI(6185) 和 NapCat WebUI(6099)
rem 断了会自动重连；关掉这个窗口 = 停止隧道
rem 打开后浏览器访问 http://127.0.0.1:6185/ （AstrBot）
rem                http://127.0.0.1:6099/webui （NapCat）
rem ============================================================
cd /d "%~dp0.."

set SERVER=%QQBOT_SERVER%
set USER=ecs-user
if not defined QQBOT_SERVER if exist "%~dp0local.bat" call "%~dp0local.bat"
if not defined QQBOT_SERVER (
  echo [错误] 没有配置服务器地址：先设置环境变量 QQBOT_SERVER，或复制 deploy\local.bat.example 为 deploy\local.bat 填上地址
  pause
  exit /b 1
)
set KEY=deploy\keys\aliyun_ed25519

if not exist "%KEY%" (
  echo [错误] 找不到私钥 %KEY%
  pause
  exit /b 1
)
where ssh >nul 2>nul
if errorlevel 1 (
  echo [错误] 没装 OpenSSH 客户端：设置 - 应用 - 可选功能 - OpenSSH 客户端
  pause
  exit /b 1
)

set /a TRY=0
:retry
set /a TRY+=1
echo.
echo [%date% %time%] 第 %TRY% 次连接 %SERVER% ...
ssh -N -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -i "%KEY%" -L 6185:127.0.0.1:6185 -L 6099:127.0.0.1:6099 %USER%@%SERVER%
echo [%date% %time%] 隧道断开，5 秒后重连（Ctrl+C 或关窗口可停止）
timeout /t 5 >nul
goto retry

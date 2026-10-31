@echo off
setlocal
rem ============================================================
rem 双击打开设置面板（本机 SSH 隧道，不经过任何 agent）
rem 已有隧道时会直接打开浏览器；关掉隧道窗口即断开
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
set LOCAL_PORT=8610
set KEY=deploy\keys\aliyun_ed25519
set URL=http://127.0.0.1:%LOCAL_PORT%/

rem 1) 隧道已经开着？直接开浏览器
curl -s -m 3 -o nul "%URL%" >nul 2>nul
if not errorlevel 1 (
  echo 检测到隧道已在运行，直接打开 %URL%
  start "" "%URL%"
  timeout /t 2 >nul
  exit /b 0
)

rem 2) 前置检查
if not exist "%KEY%" (
  echo [错误] 找不到私钥 %KEY%
  echo        请在项目根目录运行，或检查 deploy\keys\ 是否完整
  pause
  exit /b 1
)
where ssh >nul 2>nul
if errorlevel 1 (
  echo [错误] 没装 OpenSSH 客户端
  echo        设置 - 应用 - 可选功能 - 添加功能 - OpenSSH 客户端
  pause
  exit /b 1
)

rem 3) 端口被别的程序占用时提示
netstat -ano | findstr /r /c:":%LOCAL_PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo [错误] 本机 %LOCAL_PORT% 端口已被占用，可能有旧隧道没关。
  echo        查找占用：netstat -ano ^| findstr :%LOCAL_PORT%
  echo        结束进程：taskkill /PID ^<PID^> /F
  pause
  exit /b 1
)

rem 4) 建隧道（新窗口，保持不关）
echo 正在建立隧道 127.0.0.1:%LOCAL_PORT% 到 %SERVER%:8610 ...
start "qqbot-dashboard-tunnel" cmd /c ssh -N -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -i "%KEY%" -L %LOCAL_PORT%:127.0.0.1:8610 %USER%@%SERVER%

rem 5) 等端口就绪再开浏览器
for /l %%i in (1,1,10) do (
  timeout /t 1 >nul
  curl -s -m 2 -o nul "%URL%" >nul 2>nul
  if not errorlevel 1 goto :ready
)
echo [警告] 隧道还没就绪，稍后手动刷新 %URL% 试试
start "" "%URL%"
pause
exit /b 0

:ready
start "" "%URL%"
echo 已打开 %URL%
echo token 在服务器上：sudo cat /opt/qqbot-dashboard/token
echo.
echo 关闭标题为 qqbot-dashboard-tunnel 的窗口即可断开隧道。
timeout /t 5 >nul
exit /b 0

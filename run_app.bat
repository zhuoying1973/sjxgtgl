@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo       正在启动 建筑效果图业务管理系统
echo ==========================================

echo [1/3] 初始化运行环境...
set PYTHONPATH=.
:: 确保 config 目录存在(如果需要)

echo [2/3] 正在启动后台服务...
echo       (这可能需要几秒钟，请勿关闭弹出的黑窗口)
:: 使用 start /min 最小化启动后台，避免干扰用户
start /min "ArchViz Backend Service" cmd /c "python -m backend.main"

echo [3/3] 等待服务响应...
:: 等待 3 秒确保端口已监听
timeout /t 3 /nobreak >nul

echo 正在打开应用程序界面...
:: 尝试使用 Edge 的应用模式 (无地址栏，像原生软件一样)
where msedge >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    start msedge --app=http://localhost:8000
) else (
    :: 尝试 Chrome
    where chrome >nul 2>nul
    if %ERRORLEVEL% EQU 0 (
        start chrome --app=http://localhost:8000
    ) else (
        :: 回退到默认浏览器
        start http://localhost:8000
    )
)

echo.
echo ==========================================
echo        系统启动成功！
echo ==========================================
echo.
echo 注意：请保持后台服务窗口运行（可以最小化）。
echo 如需关闭系统，请直接关闭后台窗口。
echo.
timeout /t 5

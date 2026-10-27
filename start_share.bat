@echo off
chcp 65001 >nul
title 足球分析工作台 - 本机分享
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo 首次运行：正在创建 Python 虚拟环境并安装依赖，约需 3-5 分钟...
    python -m venv .venv
    if errorlevel 1 goto :fail
    ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
    if errorlevel 1 goto :fail
)

if not exist "tools\cloudflared.exe" (
    echo [提示] 未找到 tools\cloudflared.exe，将只在本机/局域网可访问。
    echo        下载地址：https://github.com/cloudflare/cloudflared/releases/latest （cloudflared-windows-amd64.exe，改名放到 tools 文件夹）
)

".venv\Scripts\python.exe" -m fwb.serve %*
goto :end

:fail
echo.
echo 安装失败：请确认已安装 Python 3.12 以上版本（https://www.python.org/downloads/ ，安装时勾选 Add to PATH）。

:end
echo.
pause

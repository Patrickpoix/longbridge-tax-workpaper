@echo off
chcp 65001>nul
setlocal enabledelayedexpansion
echo =======================
echo  Longbridge Tax Workpaper
echo =======================
echo.
where python >nul 2>nul || (echo [Error] 请先安装 Python 3.11+ & echo 下载: https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .venv (echo [1/3] 创建虚拟环境... & python -m venv .venv)
call .venv/Scripts/activate.bat
echo [2/3] 安装依赖...
python -m pip install --quiet -e . -c constraints.txt
echo [3/3] 启动...
echo.
echo 请输入月结单目录路径（可直接拖入文件夹）
set /p DIR=^> 
set /p PWD=密码（未加密则直接回车）: 
if not "%PWD%"=="" set LONGBRIDGE_PDF_PASSWORD=%PWD%
set /p YEAR=纳税年度（例如 2025，回车自动检测）: 
set /p USD=USD/CNY 年末汇率（例如 7.0288，回车跳过）: 
set /p HKD=HKD/CNY 年末汇率（例如 0.90322，回车跳过）: 
set FX=
if not "%USD%"=="" set FX=%FX% --fx USD=%USD%
if not "%HKD%"=="" set FX=%FX% --fx HKD=%HKD%
echo.
echo 正在处理，请稍候...
echo.
if not "%YEAR%"=="" (python -m longbridge_tax_workpaper "%DIR%" --output-dir outputs --tax-year %YEAR% %FX%) else (python -m longbridge_tax_workpaper "%DIR%" --output-dir outputs %FX%)
if errorlevel 1 (echo 失败) else (echo 已完成 - 输出文件在 outputs 文件夹)
echo.
pause
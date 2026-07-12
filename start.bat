@echo off
chcp 65001>nul
setlocal enabledelayedexpansion

echo =====================================
echo   Longbridge Tax Workpaper
echo   长桥证券税务工作底稿 — 一键启动
echo =====================================
echo.

REM 检查 Python
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 请先安装 Python 3.11+
    echo   下载: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 创建虚拟环境（如不存在）
if not exist ".venv" (
    echo [1/3] 首次运行：创建虚拟环境...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
)

echo [2/3] 检查并安装依赖...
call .venv\Scripts\activate.bat

REM 检查是否需要安装
python -c "import longbridge_tax_workpaper" 2>nul
if errorlevel 1 (
    python -m pip install --quiet -e . -c constraints.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络连接
        pause
        exit /b 1
    )
) else (
    echo    依赖已就绪
)

echo [3/3] 启动...
echo.
echo ============================================
echo  税务口径选择
echo ============================================
echo.
echo 提示：程序默认采用保守口径。
echo 如需调整，请在启动后使用下列参数：
echo   --cost-basis-method {FIFO,MOVING_AVERAGE,BOTH}
echo   --withholding-credit
echo   --deduct-margin-interest
echo.
echo 月结单目录可直接拖入窗口。
echo.
python -m longbridge_tax_workpaper %*

if errorlevel 1 (
    echo.
    echo 处理失败。可能是以下原因：
    echo   - 目录中没有有效的月结单 PDF
    echo   - PDF 密码错误（使用 LONGBRIDGE_PDF_PASSWORD 环境变量）
    echo   - 缺少依赖
    echo.
    echo 详情请查看上方错误信息。
    echo.
    echo 命令行示例：
    echo   set LONGBRIDGE_PDF_PASSWORD=密码
    echo   start.bat 月结单目录 --fx USD=7.19 --fx HKD=0.92
    echo   start.bat 月结单目录 --cost-basis-method FIFO --withholding-credit
) else (
    echo.
    echo ============================================
    echo  处理完成！
    echo ============================================
    echo 输出文件在 outputs 文件夹中：
    echo   - longbridge_年度_processed_results.xlsx
    echo   - longbridge_年度_workpapers.zip
    echo   - review_status_年度.json
    echo.
    echo 税务口径提示：默认采用保守计算（不抵免、不扣除）。
    echo 如需调整请重新运行并添加对应参数。
)

echo.
pause

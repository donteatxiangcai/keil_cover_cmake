@echo off
setlocal
chcp 65001 >nul
set "HERE=%~dp0"

rem 优先用装了 PySide6 的解释器
if exist "D:\miniconda3\pythonw.exe" (
    start "" "D:\miniconda3\pythonw.exe" "%HERE%main.py"
    exit /b
)
if exist "%USERPROFILE%\miniconda3\pythonw.exe" (
    start "" "%USERPROFILE%\miniconda3\pythonw.exe" "%HERE%main.py"
    exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%HERE%main.py"
    exit /b
)
where python >nul 2>nul
if %errorlevel%==0 (
    python "%HERE%main.py"
    exit /b
)

echo [!] 没有找到 Python，请先安装 Python 3.9+ 并执行: pip install pyside6
pause

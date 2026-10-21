@echo off
rem ============================================================
rem  GMusicB exe build script (run this on Windows with Python)
rem  Output: release\GMusicB\  (zip this folder and distribute)
rem ============================================================
cd /d "%~dp0"

python -m pip install --upgrade mido pyinstaller
if errorlevel 1 goto fail

python -m PyInstaller --onefile --clean --name GMusicB music_builder.py
if errorlevel 1 goto fail

if exist release rmdir /s /q release
mkdir release\GMusicB\songs
mkdir release\GMusicB\output
copy /y dist\GMusicB.exe release\GMusicB\ >nul
xcopy /y /q user_files\* release\GMusicB\ >nul

echo.
echo Done. Distribute the folder: release\GMusicB
pause
exit /b 0

:fail
echo.
echo Build failed. Check the messages above.
pause
exit /b 1

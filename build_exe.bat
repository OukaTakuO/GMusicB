@echo off
rem ============================================================
rem  GMusicB build script (folder format / run on Windows with Python)
rem  Output: release\GMusicB\  (zip this folder and distribute)
rem ============================================================
cd /d "%~dp0"

python -m pip install --upgrade mido pyinstaller
if errorlevel 1 goto fail

python make_version_info.py
if errorlevel 1 goto fail

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist GMusicB.spec del /q GMusicB.spec

python -m PyInstaller --onedir --noupx --clean --version-file version_info.txt --name GMusicB music_builder.py
if errorlevel 1 goto fail

if exist release rmdir /s /q release
xcopy /e /i /y /q dist\GMusicB release\GMusicB >nul
xcopy /y /q user_files\* release\GMusicB\ >nul
mkdir release\GMusicB\songs
mkdir release\GMusicB\output

echo.
echo Done. Zip and distribute the folder: release\GMusicB
echo (The whole folder is needed: GMusicB.exe and _internal go together.)
pause
exit /b 0

:fail
echo.
echo Build failed. Check the messages above.
pause
exit /b 1

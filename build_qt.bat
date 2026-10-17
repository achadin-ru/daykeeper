@echo off
rem Build the Qt window as a folder next to the Tk build: dist\Daykeeper-qt\ (zipped to
rem dist\Daykeeper-qt-win64.zip), with the console helper inside when dist\daykeeper-cli\ is
rem there (build.bat makes it). Fails when the folder grows past 75 MB.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3.13 -m venv .venv 2>nul || python -m venv .venv
    .venv\Scripts\python -m pip install -q --require-hashes -r requirements-build.txt
)
.venv\Scripts\python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\Daykeeper-qt packaging\daykeeper-qt.spec || exit /b 1
.venv\Scripts\python packaging\check_size.py dist\Daykeeper-qt 75 || exit /b 1
.venv\Scripts\python -c "import shutil; shutil.make_archive(r'dist\Daykeeper-qt-win64', 'zip', r'dist\Daykeeper-qt')" || exit /b 1
echo.
echo Done: %~dp0dist\Daykeeper-qt-win64.zip
certutil -hashfile dist\Daykeeper-qt-win64.zip SHA256 | findstr /v "hashfile"

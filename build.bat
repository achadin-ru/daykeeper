@echo off
rem Build the console helper (dist\daykeeper-cli\: the Claude Code hook and the journal
rem commands; a folder, since it starts three times faster than one file) and the
rem windowless app that carries it (dist\Daykeeper.exe), then print the app's SHA256.
cd /d "%~dp0"
rem One Python for the build, the tests and CI: 3.13 (pyproject.toml). The launcher picks it
rem when it is installed; otherwise whatever `python` is, and the version is printed.
if not exist .venv\Scripts\python.exe (
    py -3.13 -m venv .venv 2>nul || python -m venv .venv
    .venv\Scripts\python -m pip install -q --require-hashes -r requirements-build.txt
)
.venv\Scripts\python --version
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onedir --console --noupx --name daykeeper-cli --icon assets\icon.ico --exclude-module tkinter --exclude-module cryptography --exclude-module argon2 cli_main.py || exit /b 1
rem The app replaces an installed helper when this stamp differs.
.venv\Scripts\python -c "import time, uuid; open(r'dist\daykeeper-cli\build-id.txt', 'w').write(time.strftime('%%Y%%m%%d-%%H%%M%%S-') + uuid.uuid4().hex[:8])" || exit /b 1
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onefile --noconsole --noupx --name Daykeeper --icon assets\icon.ico --add-data "assets;assets" --add-data "dist\daykeeper-cli;daykeeper-cli" main.py || exit /b 1
echo.
echo Done: %~dp0dist\Daykeeper.exe
certutil -hashfile dist\Daykeeper.exe SHA256 | findstr /v "hashfile"

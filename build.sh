#!/bin/sh
# Build the macOS app bundle (dist/Daykeeper.app) or, on Linux, a single binary
# (dist/Daykeeper), then print its SHA256.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install -q --require-hashes -r requirements-build.txt
fi
if [ "$(uname)" = "Darwin" ]; then
    .venv/bin/python -m PyInstaller --noconfirm --clean --windowed --noupx --name Daykeeper \
        --icon assets/icon.icns --add-data "assets:assets" \
        --osx-bundle-identifier ru.achadin.daykeeper main.py
    # the version into Info.plist (PyInstaller writes 0.0.0), the minimum system, then the
    # ad-hoc signature again — the plist edit breaks the one PyInstaller made
    VERSION=$(.venv/bin/python -c "import daykeeper; print(daykeeper.__version__)")
    PLIST=dist/Daykeeper.app/Contents/Info.plist
    plutil -replace CFBundleShortVersionString -string "${VERSION%%-*}" "$PLIST"  # macOS takes digits and dots only: 1.1.2-beta.1 -> 1.1.2
    plutil -replace CFBundleVersion -string "${VERSION%%-*}" "$PLIST"
    plutil -replace LSMinimumSystemVersion -string "14.0" "$PLIST"
    codesign --force --deep --sign - dist/Daykeeper.app
    ditto -c -k --sequesterRsrc --keepParent dist/Daykeeper.app dist/Daykeeper-macos.zip
    echo "Done: $(pwd)/dist/Daykeeper.app"
    shasum -a 256 dist/Daykeeper-macos.zip
else
    .venv/bin/python -m PyInstaller --noconfirm --clean --onefile --noconsole --noupx --name Daykeeper --add-data "assets:assets" main.py
    echo "Done: $(pwd)/dist/Daykeeper"
    sha256sum dist/Daykeeper
fi

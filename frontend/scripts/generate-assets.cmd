@echo off
REM ─── Vectorae — Mobile Asset Generation (Windows) ──────────
REM Generates all required Android icon sizes and splash screen images.
REM See generate-assets.sh for full documentation on source file specifications.
REM
REM Prerequisites:
REM   npm install -g @capacitor/assets    (run once in any terminal)
REM   Place source files:
REM     frontend\assets\icon-1024.png          (1024×1024 PNG, logo in center 768px)
REM     frontend\assets\splash-2732x2732.png   (2732×2732 PNG, logo in center 512px)
REM
REM TODO: verify @capacitor/assets CLI flags for the installed version.

setlocal

set FRONTEND_DIR=%~dp0..

if not exist "%FRONTEND_DIR%\assets\icon-1024.png" (
    echo ERROR: Missing %FRONTEND_DIR%\assets\icon-1024.png
    echo        Create a 1024x1024 PNG logo file at that path, then re-run.
    exit /b 1
)

if not exist "%FRONTEND_DIR%\assets\splash-2732x2732.png" (
    echo ERROR: Missing %FRONTEND_DIR%\assets\splash-2732x2732.png
    echo        Create a 2732x2732 PNG splash file at that path, then re-run.
    exit /b 1
)

echo Generating Android icons and splash screens...

cd /d "%FRONTEND_DIR%"

REM TODO: verify exact @capacitor/assets CLI flags for the installed version
npx @capacitor/assets generate ^
  --iconBackgroundColor "#0f172a" ^
  --iconBackgroundColorDark "#0f172a" ^
  --splashBackgroundColor "#0f172a" ^
  --splashBackgroundColorDark "#0f172a" ^
  --android

echo.
echo Assets generated in android\app\src\main\res\
echo.
echo Next steps:
echo   1. npm run mobile:build   (syncs assets to Android project)
echo   2. npm run mobile:open    (open Android Studio to verify icons)

endlocal

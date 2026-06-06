@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "VENV_DIR=%PROJECT_ROOT%\.build_venv"
set "BUILD_DIR=%PROJECT_ROOT%\build"
set "DIST_DIR=%PROJECT_ROOT%\dist"
set "SPEC_PATH=%PROJECT_ROOT%\GridPlayer IPTV.spec"
set "ROOT_EXE=%PROJECT_ROOT%\GridPlayer IPTV.exe"
set "BUILT_EXE=%DIST_DIR%\GridPlayer IPTV.exe"
set "BUILD_STATUS=1"

set "ROOT_PREFIX=%PROJECT_ROOT%\"
call :strlen ROOT_PREFIX ROOT_PREFIX_LENGTH

pushd "%PROJECT_ROOT%" || exit /b 1

call :remove_path "%VENV_DIR%" || goto :fail
call :remove_path "%BUILD_DIR%" || goto :fail
call :remove_path "%DIST_DIR%" || goto :fail
call :remove_path "%SPEC_PATH%" || goto :fail

echo Creating temporary virtual environment...
python -m venv "%VENV_DIR%" || goto :fail

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Virtual environment Python was not created at "%VENV_DIR%\Scripts\python.exe"
    goto :fail
)

echo Installing build dependencies...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%PROJECT_ROOT%\requirements.txt" pyinstaller || goto :fail

echo Building executable...
"%VENV_DIR%\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name "GridPlayer IPTV" ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --collect-submodules webview ^
    --collect-data webview ^
    --collect-submodules clr_loader ^
    --collect-submodules pythonnet ^
    "app.py" || goto :fail

if not exist "%BUILT_EXE%" (
    echo PyInstaller did not create "%BUILT_EXE%"
    goto :fail
)

move /Y "%BUILT_EXE%" "%ROOT_EXE%" >nul || goto :fail
echo Executable created: "%ROOT_EXE%"
set "BUILD_STATUS=0"
goto :cleanup

:fail
set "BUILD_STATUS=1"
goto :cleanup

:cleanup
echo Cleaning temporary build files...
call :remove_path "%VENV_DIR%"
call :remove_path "%BUILD_DIR%"
call :remove_path "%DIST_DIR%"
call :remove_path "%SPEC_PATH%"
popd

if "%BUILD_STATUS%"=="0" (
    echo Done.
) else (
    echo Build failed.
)

exit /b %BUILD_STATUS%

:remove_path
set "TARGET=%~1"
if "%TARGET%"=="" exit /b 0
call :assert_under_project "%TARGET%" || exit /b 1

if exist "%TARGET%\" (
    rd /S /Q "%TARGET%" || exit /b 1
) else if exist "%TARGET%" (
    del /F /Q "%TARGET%" || exit /b 1
)

exit /b 0

:assert_under_project
set "CHECK_PATH=%~f1"
if /I "%CHECK_PATH%"=="%PROJECT_ROOT%" (
    echo Refusing to remove project root: "%CHECK_PATH%"
    exit /b 1
)

set "CHECK_PREFIX=!CHECK_PATH:~0,%ROOT_PREFIX_LENGTH%!"
if /I not "!CHECK_PREFIX!"=="%ROOT_PREFIX%" (
    echo Refusing to remove path outside project root: "%CHECK_PATH%"
    exit /b 1
)

exit /b 0

:strlen
setlocal EnableDelayedExpansion
set "STR=!%~1!"
set /A LEN=0

:strlen_loop
if defined STR (
    set "STR=!STR:~1!"
    set /A LEN+=1
    goto :strlen_loop
)

endlocal & set "%~2=%LEN%"
exit /b 0

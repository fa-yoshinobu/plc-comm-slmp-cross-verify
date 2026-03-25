@echo off
setlocal
set ROOT=%~dp0
set DOTNET_PROJECT=%ROOT%clients\dotnet\SlmpVerifyClient\SlmpVerifyClient.csproj

:MENU
cls
echo ============================================
echo  plc-comm-slmp-cross-verify
echo ============================================
echo  1. Build + Verify
echo  2. Verify only (skip build)
echo  3. Build only
echo  4. Interactive Sender
echo  5. Exit
echo ============================================
set /p CHOICE=Enter number:

if "%CHOICE%"=="1" goto BUILD_AND_VERIFY
if "%CHOICE%"=="2" goto VERIFY_ONLY
if "%CHOICE%"=="3" goto BUILD_ONLY
if "%CHOICE%"=="4" goto INTERACTIVE
if "%CHOICE%"=="5" goto EXIT
echo Invalid input.
pause
goto MENU

:BUILD_AND_VERIFY
call :DO_BUILD
if errorlevel 1 goto BUILD_ERROR
echo.
echo [Running verify...]
python "%ROOT%verify.py"
echo.
pause
goto MENU

:VERIFY_ONLY
echo.
echo [Running verify...]
python "%ROOT%verify.py"
echo.
pause
goto MENU

:BUILD_ONLY
call :DO_BUILD
if errorlevel 1 goto BUILD_ERROR
echo.
echo Build succeeded.
pause
goto MENU

:INTERACTIVE
echo.
echo [Interactive Sender]
python "%ROOT%slmp_interactive_sender.py"
goto MENU

:DO_BUILD
echo.
echo [Building] %DOTNET_PROJECT%
dotnet build "%DOTNET_PROJECT%" -c Debug
exit /b %errorlevel%

:BUILD_ERROR
echo.
echo Build failed. Skipping verify.
pause
goto MENU

:EXIT
endlocal
exit /b 0

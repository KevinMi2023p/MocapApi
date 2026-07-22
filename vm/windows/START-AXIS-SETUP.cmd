@echo off
setlocal EnableExtensions
title Axis Studio setup
cd /d "%~dp0"

echo ============================================================
echo  Axis Studio setup for the attached Windows VM
echo ============================================================
echo.
echo This helper never asks for or stores your Noitom password or Product ID.
echo.

if exist "%~dp0AxisStudioSetup.exe" goto install_exe
if exist "%~dp0AxisStudioSetup.msi" goto install_msi
if exist "%~dp0AxisStudioSetup.zip" goto install_zip
goto download

:install_exe
echo Starting the supplied Axis Studio installer...
call :run_exe "%~dp0AxisStudioSetup.exe"
set "INSTALL_RESULT=%ERRORLEVEL%"
goto install_finished

:install_msi
echo Starting the supplied Axis Studio installer...
call :run_msi "%~dp0AxisStudioSetup.msi"
set "INSTALL_RESULT=%ERRORLEVEL%"
goto install_finished

:install_zip
set "AXIS_ARCHIVE=%~dp0AxisStudioSetup.zip"
set "AXIS_EXTRACT=%TEMP%\AxisStudioSetup-%RANDOM%-%RANDOM%"
echo Extracting the supplied Axis Studio package to:
echo   "%AXIS_EXTRACT%"
powershell.exe -NoProfile -Command "Expand-Archive -LiteralPath $env:AXIS_ARCHIVE -DestinationPath $env:AXIS_EXTRACT"
if errorlevel 1 (
    echo The package could not be extracted. Open README.txt for manual steps.
    call :wait_for_user
    exit /b 1
)

set /a INSTALLER_COUNT=0
set "EXTRACTED_INSTALLER="
set "EXTRACTED_TYPE="
for /r "%AXIS_EXTRACT%" %%F in (*.msi) do call :record_installer "%%F" msi
for /r "%AXIS_EXTRACT%" %%F in (*.exe) do call :record_installer "%%F" exe

if "%INSTALLER_COUNT%"=="0" (
    echo No .msi or .exe installer was found in the package.
    echo Extracted files remain at: "%AXIS_EXTRACT%"
    call :open_folder "%AXIS_EXTRACT%"
    call :wait_for_user
    exit /b 1
)
if not "%INSTALLER_COUNT%"=="1" (
    echo Found %INSTALLER_COUNT% possible installers; none was started automatically.
    echo Choose the Axis Studio installer from: "%AXIS_EXTRACT%"
    call :open_folder "%AXIS_EXTRACT%"
    call :wait_for_user
    exit /b 2
)

echo Starting "%EXTRACTED_INSTALLER%"...
if "%EXTRACTED_TYPE%"=="msi" goto install_extracted_msi
goto install_extracted_exe

:install_extracted_msi
call :run_msi "%EXTRACTED_INSTALLER%"
set "INSTALL_RESULT=%ERRORLEVEL%"
goto install_finished

:install_extracted_exe
call :run_exe "%EXTRACTED_INSTALLER%"
set "INSTALL_RESULT=%ERRORLEVEL%"
goto install_finished

:install_finished
if "%INSTALL_RESULT%"=="0" goto install_succeeded
if "%INSTALL_RESULT%"=="3010" goto install_reboot_required
echo.
echo The installer returned exit code %INSTALL_RESULT%.
if defined AXIS_EXTRACT echo Extracted files remain at: "%AXIS_EXTRACT%"
echo Resolve any installer message, then run this helper again if needed.
call :wait_for_user
exit /b %INSTALL_RESULT%

:install_reboot_required
set "AXIS_REBOOT_REQUIRED=1"

:install_succeeded
call :cleanup_extract
goto activate

:download
echo No installer was included on this CD.
echo.
echo Your browser will open Noitom's official installation guide and account
echo portal. Register the Product ID first, then download the Axis Studio
echo edition shown in My Account and run that installer inside this VM.
call :open_url "https://support.neuronmocap.com/hc/en-us/articles/5497866781467-Installation"
call :open_url "https://account.noitom.com/"
goto instructions

:activate
echo.
echo Axis Studio installation finished.
if defined AXIS_REBOOT_REQUIRED echo IMPORTANT: Restart Windows before launching Axis Studio.
echo Your browser will now open the Noitom account portal.
call :open_url "https://account.noitom.com/"

:instructions
echo.
echo Online-license steps:
echo   1. Create or sign in to your Noitom account in the browser.
echo   2. Click Register, enter the kit's Product ID, and verify it.
echo   3. Open the ONLINE edition of Axis Studio.
echo   4. Sign in there with the same registered email and password.
echo.
echo Dongle-license users should install their matching dongle edition and
echo pass the Wibu license dongle through to this VM instead.
echo.
echo After login, return to vm/README.md on the Linux host for transceiver
echo passthrough, the 192.168.1.100 RNDIS address, and BVH broadcasting.
echo.
call :wait_for_user
exit /b 0

:record_installer
set /a INSTALLER_COUNT+=1
set "EXTRACTED_INSTALLER=%~1"
set "EXTRACTED_TYPE=%~2"
exit /b 0

:run_exe
if defined AXIS_SETUP_TEST_MODE (
    echo [test] run exe: "%~1"
    if defined AXIS_SETUP_TEST_RESULT exit /b %AXIS_SETUP_TEST_RESULT%
    exit /b 0
)
start /wait "" "%~1"
exit /b %ERRORLEVEL%

:run_msi
if defined AXIS_SETUP_TEST_MODE (
    echo [test] run msi: "%~1"
    if defined AXIS_SETUP_TEST_RESULT exit /b %AXIS_SETUP_TEST_RESULT%
    exit /b 0
)
start /wait "" msiexec.exe /i "%~1"
exit /b %ERRORLEVEL%

:open_url
if defined AXIS_SETUP_TEST_MODE (
    echo [test] open URL: "%~1"
    exit /b 0
)
start "" "%~1"
exit /b 0

:open_folder
if defined AXIS_SETUP_TEST_MODE (
    echo [test] open folder: "%~1"
    exit /b 0
)
start "" explorer.exe "%~1"
exit /b 0

:cleanup_extract
if not defined AXIS_EXTRACT exit /b 0
powershell.exe -NoProfile -Command "Remove-Item -LiteralPath $env:AXIS_EXTRACT -Recurse -Force"
if errorlevel 1 echo Warning: temporary installer files remain at "%AXIS_EXTRACT%"
set "AXIS_EXTRACT="
exit /b 0

:wait_for_user
if defined AXIS_SETUP_TEST_MODE exit /b 0
pause
exit /b 0

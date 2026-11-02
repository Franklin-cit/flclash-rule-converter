@echo off
chcp 65001 >nul
cd /d "%~dp0."
set "SKILL=%USERPROFILE%\.workbuddy-ai\skills\flclash-rule-converter"

echo ============================================
echo  flclash-rule-converter  publish
echo ============================================
echo.
echo [1/3] sync to WorkBuddy skill dir
if exist "%SKILL%\SKILL.md" goto sync
echo    skill dir not found, skip
goto commit
:sync
copy /Y "SKILL.md" "%SKILL%\SKILL.md" >nul
copy /Y "scripts\convert.py" "%SKILL%\scripts\convert.py" >nul
copy /Y "scripts\test_config.py" "%SKILL%\scripts\test_config.py" >nul
copy /Y "scripts\setup_test_env.ps1" "%SKILL%\scripts\setup_test_env.ps1" >nul
copy /Y "references\pitfalls.md" "%SKILL%\references\pitfalls.md" >nul
echo    ok

:commit
echo.
echo [2/3] commit
set "MSG=%~1"
if not "%MSG%"=="" goto haveMsg
set /p "MSG=    message: "
:haveMsg
if "%MSG%"=="" set "MSG=update"
git add -A
git commit -m "%MSG%"
if errorlevel 1 echo    nothing to commit

echo.
echo [3/3] push
git push

echo.
echo ============================================
echo  done
echo ============================================
if "%~1"=="" pause

@echo off
chcp 65001 >nul
cd /d "%~dp0."
set "SKILL=%USERPROFILE%\.workbuddy-ai\skills\flclash-rule-converter"

echo ============================================
echo   flclash-rule-converter  发布脚本
echo ============================================
echo.
echo [1/3] 同步到 WorkBuddy 技能目录
if exist "%SKILL%\SKILL.md" goto sync
echo    技能目录不存在，跳过同步
goto commit
:sync
copy /Y "SKILL.md" "%SKILL%\SKILL.md" >nul
copy /Y "scripts\convert.py" "%SKILL%\scripts\convert.py" >nul
copy /Y "scripts\test_config.py" "%SKILL%\scripts\test_config.py" >nul
copy /Y "scripts\setup_test_env.ps1" "%SKILL%\scripts\setup_test_env.ps1" >nul
copy /Y "references\pitfalls.md" "%SKILL%\references\pitfalls.md" >nul
echo    已同步 SKILL.md / scripts / references

:commit
echo.
echo [2/3] 提交到本地仓库
set "MSG=%~1"
if not "%MSG%"=="" goto haveMsg
set /p "MSG=    输入本次修改说明: "
:haveMsg
if "%MSG%"=="" set "MSG=update"
git add -A
git commit -m "%MSG%"
if errorlevel 1 echo    没有新的改动，跳过提交

echo.
echo [3/3] 推送到 GitHub
git push

echo.
echo ============================================
echo   完成
echo ============================================
if "%~1"=="" pause

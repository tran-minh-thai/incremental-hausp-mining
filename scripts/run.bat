@echo off
REM Launcher for Windows cmd.exe.
REM
REM Usage:
REM   scripts\run.bat            (all eight experiments, three trials each)
REM   scripts\run.bat 1          (only Experiment 1)
REM   scripts\run.bat 1,3,5      (selected experiments)
REM   scripts\run.bat all        (explicit form of the default)
REM   scripts\run.bat 1 --repeats 5
REM
REM Tune the heap through HEAP (default 24g, the ceiling every recorded run used):
REM   set HEAP=24g && scripts\run.bat 4
REM
REM Requirements: JDK >= 11 and mvn.cmd on PATH.

setlocal

REM Environments that must not produce measurements set HAUSP_NO_MEASURE. The Java
REM launcher enforces the same rule; this copy fails earlier and with the reason.
if not "%HAUSP_NO_MEASURE%"=="" (
    echo [run.bat] REFUSED: HAUSP_NO_MEASURE is set. Run this on the measurement machine. 1>&2
    exit /b 3
)

cd /d "%~dp0\.."

if "%HEAP%"=="" set HEAP=24g
if "%~1"=="" ( set EXP_ARG=all ) else ( set EXP_ARG=%~1 )

echo [run.bat] project root : %CD%
echo [run.bat] heap          : %HEAP%
echo [run.bat] experiments   : %EXP_ARG%
echo [run.bat] step 1/2: mvn -q package

call mvn -q package -DskipTests
if errorlevel 1 (
    echo [run.bat] Maven build failed.
    exit /b 1
)

set "JAR="
for /f "delims=" %%F in ('dir /b /o-n build\incremental-hausp-mining-*.jar 2^>nul') do (
    if not defined JAR set "JAR=build\%%F"
)

if not defined JAR (
    echo [run.bat] No fat JAR found under build\
    exit /b 1
)

rem A JAR older than the sources still produces a provenance line naming the current commit
rem with a clean tree, so the result claims to come from code that never ran. This happened on
rem 2026-09-18 and cost a whole campaign. Batch has no file-age test, so PowerShell does it.
for /f %%S in ('powershell -NoProfile -Command "$j=(Get-Item '%JAR%').LastWriteTime; $n=@(Get-ChildItem -Recurse src,pom.xml -File ^| Where-Object {$_.LastWriteTime -gt $j}); $n.Count"') do set "STALE=%%S"
if not "%STALE%"=="0" (
    echo [run.bat] REFUSED: %JAR% is older than %STALE% source file^(s^), so it is not the code
    echo [run.bat] in this working tree. Rebuild with: mvn -q package -DskipTests
    echo [run.bat] To run the old JAR on purpose, set ALLOW_STALE_JAR=1
    if not "%ALLOW_STALE_JAR%"=="1" exit /b 1
    echo [run.bat] ALLOW_STALE_JAR=1 given; continuing with the older JAR
)

shift
echo [run.bat] step 2/2: java -Xmx%HEAP% -jar %JAR% --exp %EXP_ARG% %*
java -Xmx%HEAP% -XX:+UseG1GC -jar "%JAR%" --exp %EXP_ARG% %*
exit /b %errorlevel%

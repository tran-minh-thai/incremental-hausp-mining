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
REM Tune the heap through HEAP (default 16g):
REM   set HEAP=24g && scripts\run.bat 4
REM
REM Requirements: JDK >= 11 and mvn.cmd on PATH.

setlocal

cd /d "%~dp0\.."

if "%HEAP%"=="" set HEAP=16g
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

shift
echo [run.bat] step 2/2: java -Xmx%HEAP% -jar %JAR% --exp %EXP_ARG% %*
java -Xmx%HEAP% -XX:+UseG1GC -jar "%JAR%" --exp %EXP_ARG% %*
exit /b %errorlevel%

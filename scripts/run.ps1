# Launcher for Windows PowerShell.
#
# Usage:
#   .\scripts\run.ps1                # all eight experiments, three trials each
#   .\scripts\run.ps1 1              # only Experiment 1
#   .\scripts\run.ps1 1,3,5          # selected experiments
#   .\scripts\run.ps1 all            # explicit form of the default
#   .\scripts\run.ps1 1 --repeats 5
#
# Tune the heap:
#   $env:HEAP="24g"; .\scripts\run.ps1 4
#
# Requirements: JDK >= 11 and mvn on PATH.

param(
    [Parameter(Position=0)]
    [string]$ExpArg = "all",
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Extra
)

$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $projectRoot

if (-not $env:HEAP) { $env:HEAP = "16g" }

Write-Host "[run.ps1] project root : $projectRoot"
Write-Host "[run.ps1] heap          : $($env:HEAP)"
Write-Host "[run.ps1] experiments   : $ExpArg"
Write-Host "[run.ps1] step 1/2: mvn -q package"

& mvn -q package -DskipTests
if ($LASTEXITCODE -ne 0) {
    Write-Host "[run.ps1] Maven build failed." -ForegroundColor Red
    exit 1
}

$jar = Get-ChildItem -Path "build" -Filter "incremental-hausp-mining-*.jar" -ErrorAction SilentlyContinue |
       Sort-Object Name -Descending | Select-Object -First 1

if (-not $jar) {
    Write-Host "[run.ps1] No fat JAR found under build\" -ForegroundColor Red
    exit 1
}

$jvmArgs = @("-Xmx$($env:HEAP)", "-XX:+UseG1GC", "-jar", $jar.FullName, "--exp", $ExpArg)
if ($Extra) { $jvmArgs += $Extra }
Write-Host "[run.ps1] step 2/2: java $($jvmArgs -join ' ')"
& java @jvmArgs
exit $LASTEXITCODE

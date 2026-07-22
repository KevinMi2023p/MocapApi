$ErrorActionPreference = "Stop"

$vmDir = Split-Path -Parent $PSScriptRoot
$helperSource = Join-Path $vmDir "windows\START-AXIS-SETUP.cmd"
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("axis-setup-tests-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $testRoot | Out-Null

function Assert-Contains {
    param(
        [string]$Output,
        [string]$Expected,
        [string]$CaseName
    )

    if (-not $Output.Contains($Expected)) {
        throw "[$CaseName] Expected output to contain '$Expected'. Output:`n$Output"
    }
}

function Invoke-HelperCase {
    param(
        [string]$Name,
        [scriptblock]$Arrange,
        [int]$ExpectedExitCode,
        [string[]]$ExpectedOutput,
        [string]$InstallerResult = "",
        [bool]$ExpectExtractCleanup = $false
    )

    $caseDir = Join-Path $testRoot $Name
    $caseTemp = Join-Path $caseDir "R&D (test)"
    New-Item -ItemType Directory -Path $caseDir | Out-Null
    New-Item -ItemType Directory -Path $caseTemp | Out-Null
    Copy-Item -LiteralPath $helperSource -Destination $caseDir
    & $Arrange $caseDir

    $oldTemp = $env:TEMP
    $oldTestMode = $env:AXIS_SETUP_TEST_MODE
    $oldTestResult = $env:AXIS_SETUP_TEST_RESULT
    try {
        $env:TEMP = $caseTemp
        $env:AXIS_SETUP_TEST_MODE = "1"
        if ($InstallerResult) {
            $env:AXIS_SETUP_TEST_RESULT = $InstallerResult
        } else {
            Remove-Item Env:AXIS_SETUP_TEST_RESULT -ErrorAction SilentlyContinue
        }

        $helper = Join-Path $caseDir "START-AXIS-SETUP.cmd"
        $output = (& $env:ComSpec /d /c "`"$helper`"" 2>&1 | Out-String)
        $exitCode = $LASTEXITCODE
    } finally {
        $env:TEMP = $oldTemp
        $env:AXIS_SETUP_TEST_MODE = $oldTestMode
        if ($null -eq $oldTestResult) {
            Remove-Item Env:AXIS_SETUP_TEST_RESULT -ErrorAction SilentlyContinue
        } else {
            $env:AXIS_SETUP_TEST_RESULT = $oldTestResult
        }
    }

    if ($exitCode -ne $ExpectedExitCode) {
        throw "[$Name] Expected exit $ExpectedExitCode, got $exitCode. Output:`n$output"
    }
    foreach ($expected in $ExpectedOutput) {
        Assert-Contains -Output $output -Expected $expected -CaseName $Name
    }
    if ($ExpectExtractCleanup) {
        $leftovers = @(Get-ChildItem -LiteralPath $caseTemp -Directory -Filter "AxisStudioSetup-*")
        if ($leftovers.Count -ne 0) {
            throw "[$Name] Expected extracted installer directory to be removed."
        }
    }
}

try {
    $case = @{
        Name = "download"
        Arrange = { param($caseDir) }
        ExpectedExitCode = 0
        ExpectedOutput = @(
            "No installer was included",
            "[test] open URL: `"https://account.noitom.com/`""
        )
    }
    Invoke-HelperCase @case

    $case = @{
        Name = "direct-msi"
        Arrange = {
            param($caseDir)
            New-Item -ItemType File -Path (Join-Path $caseDir "AxisStudioSetup.msi") | Out-Null
        }
        ExpectedExitCode = 0
        ExpectedOutput = @("[test] run msi:", "Axis Studio installation finished")
    }
    Invoke-HelperCase @case

    $case = @{
        Name = "reboot-required"
        Arrange = {
            param($caseDir)
            New-Item -ItemType File -Path (Join-Path $caseDir "AxisStudioSetup.msi") | Out-Null
        }
        ExpectedExitCode = 0
        InstallerResult = "3010"
        ExpectedOutput = @("IMPORTANT: Restart Windows before launching Axis Studio")
    }
    Invoke-HelperCase @case

    $case = @{
        Name = "single-installer-zip"
        Arrange = {
            param($caseDir)
            $source = Join-Path $caseDir "zip-source"
            New-Item -ItemType Directory -Path $source | Out-Null
            New-Item -ItemType File -Path (Join-Path $source "AxisStudio.msi") | Out-Null
            Compress-Archive -Path (Join-Path $source "*") -DestinationPath (Join-Path $caseDir "AxisStudioSetup.zip")
            Remove-Item -LiteralPath $source -Recurse -Force
        }
        ExpectedExitCode = 0
        ExpectExtractCleanup = $true
        ExpectedOutput = @("[test] run msi:", "Axis Studio installation finished")
    }
    Invoke-HelperCase @case

    $case = @{
        Name = "ambiguous-installer-zip"
        Arrange = {
            param($caseDir)
            $source = Join-Path $caseDir "zip-source"
            New-Item -ItemType Directory -Path $source | Out-Null
            New-Item -ItemType File -Path (Join-Path $source "Dependency.msi") | Out-Null
            New-Item -ItemType File -Path (Join-Path $source "AxisStudio.exe") | Out-Null
            Compress-Archive -Path (Join-Path $source "*") -DestinationPath (Join-Path $caseDir "AxisStudioSetup.zip")
            Remove-Item -LiteralPath $source -Recurse -Force
        }
        ExpectedExitCode = 2
        ExpectedOutput = @(
            "Found 2 possible installers; none was started automatically",
            "[test] open folder:"
        )
    }
    Invoke-HelperCase @case

    Write-Host "All Windows Axis setup helper tests passed."
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}

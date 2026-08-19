[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version1Bundle,

    [Parameter(Mandatory = $true)]
    [string]$Version2Bundle,

    [Parameter(Mandatory = $true)]
    [string]$RollbackProbeBundle,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedVersion1,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedVersion2,

    [string]$LogDirectory = (
        Join-Path $env:TEMP ("OmniBaseInstallerE2E-" + [DateTime]::UtcNow.ToString("yyyyMMddHHmmss"))
    ),

    [switch]$DisposableAccountAcknowledged
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $DisposableAccountAcknowledged -or $env:OMNIBASE_INSTALLER_E2E -ne "1") {
    throw "installer_e2e_requires_disposable_account_acknowledgement"
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "installer_e2e_requires_non_elevated_shell"
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "installer_e2e_local_app_data_unavailable"
}

$InstallRoot = Join-Path $env:LOCALAPPDATA "Programs\OmniBase"
$DataRoot = Join-Path $env:LOCALAPPDATA "OmniBase"
$DataMarker = Join-Path $DataRoot "installer-lifecycle-retained.marker"
$InstalledExecutable = Join-Path $InstallRoot "OmniBase.exe"

function Test-FullyQualifiedWindowsPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $driveRooted = [Text.RegularExpressions.Regex]::IsMatch(
        $Path,
        "\A[A-Za-z]:\\"
    )
    $uncRooted = [Text.RegularExpressions.Regex]::IsMatch(
        $Path,
        "\A\\\\[^\\]+\\[^\\]+(?:\\|\z)"
    )
    return $driveRooted -or $uncRooted
}

function Assert-OrdinaryBundle {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-FullyQualifiedWindowsPath -Path $Path)) {
        throw "installer_e2e_bundle_path_must_be_absolute"
    }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "installer_e2e_bundle_must_be_regular_file"
    }
    if ($item.Extension -ine ".exe") {
        throw "installer_e2e_bundle_must_be_exe"
    }
}

function Get-OmniBaseRegistration {
    $roots = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    return @(
        foreach ($root in $roots) {
            Get-ItemProperty -Path $root -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.DisplayName -eq "OmniBase" -and
                    $_.Publisher -eq "OmniBase Contributors"
                }
        }
    )
}

function Assert-InstalledVersion {
    param([Parameter(Mandatory = $true)][string]$Expected)

    $registrations = @(Get-OmniBaseRegistration)
    if ($registrations.Count -ne 1) {
        throw "installer_e2e_registration_count_invalid"
    }
    $actualVersion = [version]$registrations[0].DisplayVersion
    $expectedVersion = [version]$Expected
    $actualTriple = "$($actualVersion.Major).$($actualVersion.Minor).$($actualVersion.Build)"
    $expectedTriple = "$($expectedVersion.Major).$($expectedVersion.Minor).$($expectedVersion.Build)"
    if ($actualTriple -ne $expectedTriple) {
        throw "installer_e2e_version_mismatch"
    }
    if (-not (Test-Path -LiteralPath $InstalledExecutable -PathType Leaf)) {
        throw "installer_e2e_executable_missing"
    }
}

function Assert-RetainedData {
    if (-not (Test-Path -LiteralPath $DataMarker -PathType Leaf)) {
        throw "installer_e2e_user_data_was_not_retained"
    }
}

function Invoke-Bundle {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][ValidateSet("install", "uninstall")][string]$Action,
        [Parameter(Mandatory = $true)][string]$LogName
    )

    $logPath = Join-Path $LogDirectory $LogName
    $arguments = @(
        "/$Action",
        "/quiet",
        "/norestart",
        "/log",
        "`"$logPath`""
    )
    $process = Start-Process -FilePath $Path -ArgumentList $arguments -Wait -PassThru
    return $process.ExitCode
}

function Assert-Success {
    param([Parameter(Mandatory = $true)][int]$ExitCode)

    if ($ExitCode -ne 0) {
        throw "installer_e2e_expected_success"
    }
}

function Assert-Failure {
    param([Parameter(Mandatory = $true)][int]$ExitCode)

    if ($ExitCode -eq 0) {
        throw "installer_e2e_expected_failure"
    }
}

function Assert-RollbackExecutionEvidence {
    param([Parameter(Mandatory = $true)][string]$LogName)

    $logPath = Join-Path $LogDirectory $LogName
    if (-not (Test-Path -LiteralPath $logPath -PathType Leaf)) {
        throw "installer_e2e_rollback_log_missing"
    }
    $log = [IO.File]::ReadAllText($logPath)
    foreach ($required in @(
        "Starting a new MSI transaction, id: OmniBaseRollbackProbeTransaction",
        "Applying execute package: OmniBaseUpgradeCandidate",
        "Applied execute package: OmniBaseUpgradeCandidate, result: 0x0",
        "Applying execute package: IntentionalRollbackBlocker",
        "Applied execute package: IntentionalRollbackBlocker, result: 0x80070643",
        "Rolling back MSI transaction, id: OmniBaseRollbackProbeTransaction",
        "package: OmniBaseUpgradeCandidate, install registration state: Absent, cache registration state: Absent"
    )) {
        if ($log.IndexOf($required, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "installer_e2e_rollback_execution_unproven"
        }
    }
}

foreach ($bundle in @($Version1Bundle, $Version2Bundle, $RollbackProbeBundle)) {
    Assert-OrdinaryBundle -Path $bundle
}
if ((Test-Path -LiteralPath $InstallRoot) -or @(Get-OmniBaseRegistration).Count -ne 0) {
    throw "installer_e2e_requires_clean_product_state"
}
if (Test-Path -LiteralPath $DataRoot) {
    throw "installer_e2e_requires_clean_dedicated_user_data_root"
}

New-Item -ItemType Directory -Path $LogDirectory | Out-Null

Write-Output "STAGE InstallVersion1"
Assert-Success (Invoke-Bundle -Path $Version1Bundle -Action install -LogName "01-install-v1.log")
Assert-InstalledVersion -Expected $ExpectedVersion1
New-Item -ItemType Directory -Path $DataRoot | Out-Null
[IO.File]::WriteAllText($DataMarker, "retain across upgrade, rollback, and uninstall")

Write-Output "STAGE UpgradeVersion2"
Assert-Success (Invoke-Bundle -Path $Version2Bundle -Action install -LogName "02-upgrade-v2.log")
Assert-InstalledVersion -Expected $ExpectedVersion2
Assert-RetainedData

Write-Output "STAGE RejectDowngrade"
Assert-Failure (Invoke-Bundle -Path $Version1Bundle -Action install -LogName "03-downgrade-v1.log")
Assert-InstalledVersion -Expected $ExpectedVersion2
Assert-RetainedData

Write-Output "STAGE RollbackFailedUpgrade"
Assert-Failure (
    Invoke-Bundle -Path $RollbackProbeBundle -Action install -LogName "04-rollback-probe.log"
)
Assert-RollbackExecutionEvidence -LogName "04-rollback-probe.log"
Assert-InstalledVersion -Expected $ExpectedVersion2
Assert-RetainedData

Write-Output "STAGE UninstallVersion2"
Assert-Success (
    Invoke-Bundle -Path $Version2Bundle -Action uninstall -LogName "05-uninstall-v2.log"
)
if (@(Get-OmniBaseRegistration).Count -ne 0) {
    throw "installer_e2e_registration_remained_after_uninstall"
}
if (Test-Path -LiteralPath $InstalledExecutable) {
    throw "installer_e2e_executable_remained_after_uninstall"
}
if (Test-Path -LiteralPath $InstallRoot) {
    throw "installer_e2e_install_root_remained_after_uninstall"
}
Assert-RetainedData

[ordered]@{
    result = "passed"
    install_root_removed = $true
    user_data_retained = $true
    data_marker = $DataMarker
    log_directory = $LogDirectory
} | ConvertTo-Json -Compress

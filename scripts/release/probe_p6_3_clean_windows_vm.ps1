[CmdletBinding()]
param(
    [Parameter()]
    [ValidatePattern('^[A-Za-z0-9._-]{1,64}$')]
    [string]$VmName = 'OmniBase-P63-Clean-Windows',

    [Parameter()]
    [ValidateRange(1073741824, 1099511627776)]
    [long]$MinimumHostFreeBytes = 21474836480
)

$ErrorActionPreference = 'Stop'
$expectedVmName = 'OmniBase-P63-Clean-Windows'
$blockers = [System.Collections.Generic.List[string]]::new()
$hostIsWindows = $env:OS -eq 'Windows_NT' -or
    [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
$facts = [ordered]@{
    schema_version = 1
    vm_name = $VmName
    host_windows = $hostIsWindows
    hyper_v_cmdlets_available = $false
    exact_vm_found = $false
    vm_off = $false
    generation_2 = $false
    checkpoints_absent = $false
    exact_single_vhdx = $false
    vhd_detached = $false
    path_reparse_free = $false
    owner_verified = $false
    broad_write_acl_absent = $false
    host_free_space_sufficient = $false
    guest_freshness_proven = $false
    mutation_performed = $false
}

function Add-Blocker {
    param([Parameter(Mandatory)][string]$Code)
    if (-not $blockers.Contains($Code)) {
        $blockers.Add($Code)
    }
}

function Test-ReparseFreeExistingPath {
    param([Parameter(Mandatory)][string]$LiteralPath)
    $current = Get-Item -LiteralPath $LiteralPath -Force
    while ($null -ne $current) {
        if (($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            return $false
        }
        $parent = Split-Path -LiteralPath $current.FullName -Parent
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $current.FullName) {
            break
        }
        $current = Get-Item -LiteralPath $parent -Force
    }
    return $true
}

function Test-AclBoundary {
    param([Parameter(Mandatory)][string]$LiteralPath)
    $acl = Get-Acl -LiteralPath $LiteralPath
    $currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $allowedOwnerSids = @(
        $currentSid,
        'S-1-5-18',
        'S-1-5-32-544'
    )
    try {
        $ownerSid = ([System.Security.Principal.NTAccount]$acl.Owner).Translate(
            [System.Security.Principal.SecurityIdentifier]
        ).Value
        $ownerVerified = $allowedOwnerSids -contains $ownerSid
    } catch {
        $ownerVerified = $false
    }
    $broadSids = @(
        'S-1-1-0',
        'S-1-5-32-545',
        'S-1-5-11'
    )
    $writeMask = [System.Security.AccessControl.FileSystemRights]::Write -bor
        [System.Security.AccessControl.FileSystemRights]::Modify -bor
        [System.Security.AccessControl.FileSystemRights]::FullControl
    $unsafe = $acl.Access | Where-Object {
        if ($_.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
            (($_.FileSystemRights -band $writeMask) -eq 0)) {
            return $false
        }
        try {
            $sid = $_.IdentityReference.Translate(
                [System.Security.Principal.SecurityIdentifier]
            ).Value
            return $broadSids -contains $sid
        } catch {
            return $true
        }
    }
    return [pscustomobject]@{
        OwnerVerified = $ownerVerified
        BroadWriteAbsent = @($unsafe).Count -eq 0
    }
}

try {
    if ($VmName -cne $expectedVmName) {
        Add-Blocker 'VM_NAME_NOT_AUTHORIZED'
    } elseif (-not $hostIsWindows) {
        Add-Blocker 'WINDOWS_HOST_REQUIRED'
    } else {
        $required = @('Get-VM', 'Get-VMHardDiskDrive', 'Get-VHD', 'Get-VMSnapshot')
        $missing = @($required | Where-Object { $null -eq (Get-Command $_ -ErrorAction SilentlyContinue) })
        if ($missing.Count -ne 0) {
            Add-Blocker 'HYPER_V_READONLY_CMDLETS_UNAVAILABLE'
        } else {
            $facts.hyper_v_cmdlets_available = $true
            $matches = @(Get-VM -Name $VmName -ErrorAction SilentlyContinue)
            if ($matches.Count -ne 1) {
                Add-Blocker 'DEDICATED_CLEAN_WINDOWS_VM_NOT_FOUND'
            } else {
                $vm = $matches[0]
                $facts.exact_vm_found = $true
                $facts.vm_off = $vm.State -eq 'Off'
                $facts.generation_2 = $vm.Generation -eq 2
                if (-not $facts.vm_off) { Add-Blocker 'VM_MUST_BE_OFF_FOR_PREFLIGHT' }
                if (-not $facts.generation_2) { Add-Blocker 'VM_GENERATION_NOT_ACCEPTED' }

                $snapshots = @(Get-VMSnapshot -VMName $VmName -ErrorAction Stop)
                $facts.checkpoints_absent = $snapshots.Count -eq 0
                if (-not $facts.checkpoints_absent) { Add-Blocker 'VM_CHECKPOINT_STATE_AMBIGUOUS' }

                $attachments = @(Get-VMHardDiskDrive -VMName $VmName -ErrorAction Stop)
                if ($attachments.Count -ne 1 -or
                    [System.IO.Path]::GetExtension($attachments[0].Path) -ne '.vhdx') {
                    Add-Blocker 'EXACT_SINGLE_VHDX_NOT_PROVEN'
                } else {
                    $facts.exact_single_vhdx = $true
                    $resolved = (Resolve-Path -LiteralPath $attachments[0].Path -ErrorAction Stop).Path
                    $vhdFile = Get-Item -LiteralPath $resolved -Force
                    $vhd = Get-VHD -Path $resolved -ErrorAction Stop
                    $facts.vhd_detached = -not $vhd.Attached
                    if (-not $facts.vhd_detached) { Add-Blocker 'VHDX_ATTACHED_OR_MOUNTED' }

                    $facts.path_reparse_free = Test-ReparseFreeExistingPath -LiteralPath $resolved
                    if (-not $facts.path_reparse_free) { Add-Blocker 'VHDX_REPARSE_PATH_FORBIDDEN' }

                    $aclFacts = Test-AclBoundary -LiteralPath $resolved
                    $facts.owner_verified = $aclFacts.OwnerVerified
                    $facts.broad_write_acl_absent = $aclFacts.BroadWriteAbsent
                    if (-not $facts.owner_verified) { Add-Blocker 'VHDX_OWNER_NOT_VERIFIED' }
                    if (-not $facts.broad_write_acl_absent) { Add-Blocker 'VHDX_BROAD_WRITE_ACL_PRESENT' }

                    $drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($vhdFile.FullName))
                    $facts.host_free_space_sufficient =
                        $drive.DriveType -ne [System.IO.DriveType]::Network -and
                        $drive.AvailableFreeSpace -ge $MinimumHostFreeBytes
                    if (-not $facts.host_free_space_sufficient) {
                        Add-Blocker 'HOST_DISK_OR_FREE_SPACE_NOT_ACCEPTED'
                    }
                }

                Add-Blocker 'GUEST_FRESHNESS_AND_INSTALL_ACCEPTANCE_NOT_PROVEN'
            }
        }
    }
} catch {
    Add-Blocker 'READONLY_VM_PREFLIGHT_INSPECTION_FAILED'
}

$ready = $blockers.Count -eq 1 -and
    $blockers[0] -eq 'GUEST_FRESHNESS_AND_INSTALL_ACCEPTANCE_NOT_PROVEN'
if ($ready) {
    Write-Output 'CLEAN_WINDOWS_VM_PREFLIGHT_READY'
}
Write-Output 'CLEAN_WINDOWS_VM_ACCEPTANCE_NOT_PROVEN'
Write-Output 'NO_VM_OR_VIRTUAL_DISK_MUTATION_PERFORMED'
[ordered]@{
    schema_version = 1
    status = if ($ready) { 'preflight_ready_guest_acceptance_pending' } else { 'preflight_blocked' }
    blockers = @($blockers)
    facts = $facts
} | ConvertTo-Json -Depth 5 -Compress

if ($ready) { exit 10 }
exit 20

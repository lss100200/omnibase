[CmdletBinding()]
param(
    [string]$ArtifactRoot = 'C:\tmp\omnibase-p345-overlay-gate',
    [switch]$KeepProject,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$project = 'omnibase-p345-overlay-gate'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$composeFile = (Resolve-Path (Join-Path $repoRoot 'deployment\overlay\compose.disposable.yml')).Path
$envFile = (Resolve-Path (Join-Path $repoRoot 'deployment\overlay\gate.env')).Path
$runId = 'run-{0}' -f (Get-Date -Format 'yyyyMMdd-HHmmss')
$artifactDirectory = Join-Path $ArtifactRoot $runId
$null = New-Item -ItemType Directory -Path $artifactDirectory -Force
$sourceManifestPath = Join-Path $artifactDirectory 'source-manifest.json'

function Invoke-GateCommand {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & docker @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "docker command failed with exit code ${exitCode}: docker $($Arguments -join ' ')`n$($output -join "`n")"
    }
    return @($output)
}

function Compose-Arguments {
    param([string[]]$Tail)

    return @(
        'compose',
        '--env-file', $envFile,
        '-p', $project,
        '-f', $composeFile
    ) + $Tail
}

function New-DisposableHeadscaleApiKey {
    $arguments = Compose-Arguments @(
        'exec', '-T', 'headscale',
        'headscale', 'apikeys', 'create', '--expiration', '30m', '--output', 'json'
    )
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw = & docker @arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw 'Headscale disposable API key creation failed'
    }
    try {
        $decoded = (($raw -join "`n").Trim() | ConvertFrom-Json)
    }
    catch {
        throw 'Headscale disposable API key response was not valid JSON'
    }
    if ($decoded -isnot [string] -or [string]::IsNullOrWhiteSpace($decoded)) {
        throw 'Headscale disposable API key response shape was rejected'
    }
    return [string]$decoded
}

function Set-DisposableProviderSecret {
    param(
        [Parameter(Mandatory)]
        [string]$ApiKey
    )

    $pythonSource = @'
import os
import pathlib
import sys

target = pathlib.Path("/run/omnibase-provider-secrets/headscale-api-key")
target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
value = sys.stdin.read().strip()
if not value or len(value) > 512 or any(character.isspace() for character in value):
    raise SystemExit(23)
temporary = target.with_suffix(".tmp")
temporary.write_text(value, encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, target)
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pythonSource))
    $bootstrap = "import base64;exec(base64.b64decode('$encoded'))"
    $arguments = Compose-Arguments @(
        'run', '--rm', '--no-deps', '-T',
        'provider-secret-init', 'python', '-c', $bootstrap
    )
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = $ApiKey | & docker @arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw 'Disposable provider secret injection failed'
    }
    Remove-Variable output -ErrorAction SilentlyContinue
}

$result = [ordered]@{
    run_id = $runId
    project = $project
    started_at = (Get-Date).ToUniversalTime().ToString('o')
    headscale_image = 'headscale/headscale@sha256:ea9b5ee06274d757a4d52103de56cd11a9c393acb19d9a35f4b9fe52ada410de'
    node_daemon_image = 'python@sha256:f9ce6fe33d9a5499e35c976df16d24ae80f6ef0a28be5433140236c2ca482686'
    pki_image = 'alpine/openssl@sha256:5008e829163320a6e8166883c03e68189e8925ade68cde36584dc2a41cfa5248'
    gate_runner_base_image = 'python@sha256:f9ce6fe33d9a5499e35c976df16d24ae80f6ef0a28be5433140236c2ca482686'
    gate_runner_image_id = $null
    gate_runner_build = 'not_run'
    compose_env_file = 'deployment/overlay/gate.env'
    source_manifest = 'source-manifest.json'
    source_manifest_sha256 = $null
    source_tree_sha256 = $null
    source_git_commit = $null
    source_git_tree = $null
    source_git_dirty = $null
    source_git_dirty_scope_sha256 = $null
    real_member_devices_registered = 0
    host_ports_published = 0
    business_database_accessed = $false
    root_env_accessed_by_script = $false
    lifecycle_gate = 'not_run'
    offline_gate = 'not_run'
    reconnect_gate = 'not_run'
    headscale_gate = 'not_run'
    headscale_provider_mutation_gate = 'not_run'
    provider_api_key_injected = $false
    containment_scan = 'not_run'
    configuration_seal = 'not_run'
    cleanup_gate = 'not_run'
}

$validator = Join-Path $repoRoot 'scripts\overlay\validate_disposable_gate.py'
& python -B $validator --repo-root $repoRoot --manifest-out $sourceManifestPath | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Disposable Gate source seal validator failed'
}
$sourceManifest = Get-Content -LiteralPath $sourceManifestPath -Raw | ConvertFrom-Json
$result.configuration_seal = 'passed'
$result.source_manifest_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceManifestPath).Hash.ToLowerInvariant()
$result.source_tree_sha256 = [string]$sourceManifest.source_tree_sha256
$result.source_git_commit = [string]$sourceManifest.git.commit
$result.source_git_tree = [string]$sourceManifest.git.tree
$result.source_git_dirty = [bool]$sourceManifest.git.dirty
$result.source_git_dirty_scope_sha256 = [string]$sourceManifest.git.dirty_scope_sha256

if ($ValidateOnly) {
    $result.completed_at = (Get-Date).ToUniversalTime().ToString('o')
    $result.status = 'validation_only_passed'
    $result | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath (Join-Path $artifactDirectory 'report.json') -Encoding utf8
    Write-Output "P34.5C disposable Overlay Gate validation passed: $artifactDirectory"
    return
}

try {
    Invoke-GateCommand (Compose-Arguments @('build', '--pull', 'gate-runner')) | Out-Null
    $result.gate_runner_build = 'passed'
    $actualGateRunnerImage = (& docker image inspect omnibase-p345-overlay-gate-runner:source --format '{{.Id}}').Trim()
    if ($LASTEXITCODE -ne 0 -or $actualGateRunnerImage -notmatch '^sha256:[0-9a-f]{64}$') {
        throw 'The source-built disposable Gate Runner image is unavailable'
    }
    $result.gate_runner_image_id = $actualGateRunnerImage
    & python -B $validator --repo-root $repoRoot --verify-manifest $sourceManifestPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Disposable Gate source changed during the image build'
    }
    $composeSource = Get-Content -LiteralPath $composeFile -Raw
    if ($composeSource -match '\$\{OMNIBASE_GATE_' -or $composeSource -notmatch 'image:\s+omnibase-p345-overlay-gate-runner:source') {
        throw 'Disposable Compose still permits Gate Runner environment substitution'
    }
    Invoke-GateCommand (Compose-Arguments @('down', '-v', '--remove-orphans')) | Out-Null
    Invoke-GateCommand (Compose-Arguments @('up', '-d', 'pki-init', 'headscale')) | Out-Null

    $headscaleReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            Invoke-GateCommand (Compose-Arguments @(
                    'exec', '-T', 'headscale', 'headscale', 'users', 'create', 'omnibase-gate'
                )) | Out-Null
            $headscaleReady = $true
            break
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $headscaleReady) {
        throw 'Headscale did not accept a disposable control-plane mutation'
    }
    $usersJson = Invoke-GateCommand (Compose-Arguments @(
            'exec', '-T', 'headscale', 'headscale', 'users', 'list', '--output', 'json'
        ))
    $users = (($usersJson -join "`n") | ConvertFrom-Json)
    if (@($users).Count -ne 1 -or $users[0].name -ne 'omnibase-gate') {
        throw 'Headscale disposable user evidence did not match the sealed expectation'
    }
    $apiKey = New-DisposableHeadscaleApiKey
    try {
        Set-DisposableProviderSecret -ApiKey $apiKey
        $result.provider_api_key_injected = $true
    }
    finally {
        Remove-Variable apiKey -ErrorAction SilentlyContinue
    }
    Invoke-GateCommand (Compose-Arguments @('up', '-d', 'node-daemon')) | Out-Null
    $result.headscale_gate = 'passed'

    $lifecycle = Invoke-GateCommand (Compose-Arguments @('run', '--rm', 'gate-runner'))
    if (($lifecycle -join "`n") -notmatch '1 passed') {
        throw 'Overlay lifecycle Gate did not report one passing test'
    }
    $result.lifecycle_gate = 'passed'
    $providerEvidenceLine = @($lifecycle | Where-Object {
            $_ -is [string] -and $_.StartsWith('P34_5_PROVIDER_EVIDENCE=')
        })
    if ($providerEvidenceLine.Count -ne 1) {
        throw 'Overlay lifecycle Gate did not emit exactly one provider evidence record'
    }
    $providerEvidence = (
        $providerEvidenceLine[0].Substring('P34_5_PROVIDER_EVIDENCE='.Length) |
            ConvertFrom-Json
    )
    if (
        -not $providerEvidence.activate_created_real_record -or
        -not $providerEvidence.rotate_expired_old_record -or
        -not $providerEvidence.rotate_created_active_record -or
        -not $providerEvidence.ambiguous_mutation_not_replayed -or
        -not $providerEvidence.revoke_expired_current_record -or
        -not $providerEvidence.status_used_headscale_truth -or
        -not $providerEvidence.receipts_redacted
    ) {
        throw 'Headscale provider mutation evidence did not satisfy the sealed lifecycle'
    }
    $result.headscale_provider_mutation_gate = 'passed'
    $result.provider_evidence = [ordered]@{
        activate_created_real_record = $true
        rotate_expired_old_record = $true
        rotate_created_active_record = $true
        ambiguous_mutation_not_replayed = $true
        revoke_expired_current_record = $true
        status_used_headscale_truth = $true
        receipts_redacted = $true
        provider_record_count = [int]$providerEvidence.provider_record_count
        provider_api_mutation_count = [int]$providerEvidence.provider_api_mutation_count
    }

    Invoke-GateCommand (Compose-Arguments @('stop', 'node-daemon')) | Out-Null
    $offline = Invoke-GateCommand (Compose-Arguments @(
            'run', '--rm', '--no-deps',
            '-e', 'OMNIBASE_OVERLAY_GATE_EXPECT_OFFLINE=1',
            'gate-runner'
        ))
    if (($offline -join "`n") -notmatch '1 passed, 2 skipped') {
        throw 'Node-Daemon offline Gate did not report the sealed result'
    }
    $result.offline_gate = 'passed'

    Invoke-GateCommand (Compose-Arguments @('start', 'node-daemon')) | Out-Null
    Start-Sleep -Seconds 2
    $reconnect = Invoke-GateCommand (Compose-Arguments @(
            'run', '--rm', '--no-deps',
            '-e', 'OMNIBASE_OVERLAY_GATE_EXPECT_RECONNECTED=1',
            'gate-runner'
        ))
    if (($reconnect -join "`n") -notmatch '1 passed, 2 skipped') {
        throw 'Node-Daemon reconnect Gate did not report the sealed result'
    }
    $result.reconnect_gate = 'passed'

    $logs = Invoke-GateCommand (Compose-Arguments @('logs', '--no-color', 'headscale', 'node-daemon'))
    $inspect = & docker inspect "${project}-headscale-1" "${project}-node-daemon-1" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw 'Disposable container inspection failed'
    }
    $scanText = ($logs + $inspect) -join "`n"
    $forbiddenPatterns = @(
        '(?i)authorization\s*:',
        '(?i)bearer\s+[a-z0-9._-]+',
        '(?i)cookie\s*:',
        '-----BEGIN [A-Z ]*PRIVATE KEY-----',
        '(?i)tskey-[a-z0-9-]+',
        '(?i)sk-[a-z0-9._-]{12,}',
        '(?i)https?://[^/\s:@]+:[^/\s@]+@'
    )
    foreach ($pattern in $forbiddenPatterns) {
        if ($scanText -match $pattern) {
            throw "Disposable container containment scan matched forbidden pattern: $pattern"
        }
    }

    $sourceScanPaths = @(
        (Join-Path $repoRoot 'scripts\overlay'),
        (Join-Path $repoRoot 'deployment\overlay'),
        $artifactDirectory
    )
    foreach ($sourcePath in $sourceScanPaths) {
        Get-ChildItem -LiteralPath $sourcePath -File -Recurse |
            Where-Object {
                $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and
                $_.Extension -in @('.env', '.json', '.md', '.ps1', '.py', '.txt', '.yaml', '.yml')
            } |
            ForEach-Object {
            $text = Get-Content -LiteralPath $_.FullName -Raw -ErrorAction SilentlyContinue
            foreach ($pattern in $forbiddenPatterns) {
                if ($text -match $pattern) {
                    throw "Containment scan matched forbidden material in $($_.FullName)"
                }
            }
        }
    }
    $result.containment_scan = 'passed'
    & python -B $validator --repo-root $repoRoot --verify-manifest $sourceManifestPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Disposable Gate source changed while the Gate was running'
    }
    $result.completed_at = (Get-Date).ToUniversalTime().ToString('o')
    $result.status = 'passed'
}
catch {
    $result.completed_at = (Get-Date).ToUniversalTime().ToString('o')
    $result.status = 'failed'
    $result.failure = $_.Exception.Message
    throw
}
finally {
    $reportPath = Join-Path $artifactDirectory 'report.json'
    if (-not $KeepProject) {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $cleanupOutput = & docker compose --env-file $envFile -p $project -f $composeFile down -v --remove-orphans 2>&1
            $cleanupExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        $remainingContainers = @(& docker ps -a --filter "label=com.docker.compose.project=$project" -q)
        $remainingNetworks = @(& docker network ls --filter "label=com.docker.compose.project=$project" -q)
        $remainingVolumes = @(& docker volume ls --filter "label=com.docker.compose.project=$project" -q)
        $result.cleanup = [ordered]@{
            compose_down_exit_code = $cleanupExitCode
            remaining_containers = @($remainingContainers | Where-Object { $_ }).Count
            remaining_networks = @($remainingNetworks | Where-Object { $_ }).Count
            remaining_disposable_volumes = @($remainingVolumes | Where-Object { $_ }).Count
        }
        if (
            $cleanupExitCode -eq 0 -and
            $result.cleanup.remaining_containers -eq 0 -and
            $result.cleanup.remaining_networks -eq 0 -and
            $result.cleanup.remaining_disposable_volumes -eq 0
        ) {
            $result.cleanup_gate = 'passed'
        }
        else {
            $result.cleanup_gate = 'failed'
            $result.status = 'failed'
            $result.failure = 'Disposable Overlay Gate cleanup was incomplete'
        }
        Remove-Variable cleanupOutput -ErrorAction SilentlyContinue
    }
    else {
        $result.cleanup_gate = 'skipped_keep_project'
    }
    $result | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $reportPath -Encoding utf8
    if ($result.cleanup_gate -eq 'failed') {
        throw 'Disposable Overlay Gate cleanup was incomplete'
    }
}

Write-Output "P34.5C disposable Overlay Gate passed: $artifactDirectory"

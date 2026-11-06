<#
.SYNOPSIS
    Uninstall per-user v1.x (Elliott's Caspar Controller) once v2 has taken over its config (#12).

.DESCRIPTION
    Does nothing until v2's config says "migrated_from": "1.x". Then, for every user profile,
    stops v1.x's exe and runs its silent uninstaller. CasparCG keeps running: v2 has adopted it.
    Run as SYSTEM (an Intune remediation, #39) or by hand as an admin. -WhatIf shows what it
    would do. Exit code 0: nothing left to do; 1: something failed (see the output).
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$ConfigPath = (Join-Path $env:ProgramData 'CasparController\config.json'),
    [string]$UsersDir = (Join-Path $env:SystemDrive 'Users')
)

$V1Folder = "AppData\Local\Programs\Elliott's Caspar Controller"
$V1Exe = 'ElliottsCasparController.exe'
$V1UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A3F7C2D1-8B4E-4F9A-B2C6-E1D8A3F7C2D1}_is1'

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    Write-Output "No v2 config at $ConfigPath yet: leaving v1.x alone."
    exit 0
}
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($config.migrated_from -ne '1.x') {
    Write-Output "v2 hasn't imported a v1.x config: leaving v1.x alone."
    exit 0
}

function Remove-UninstallEntry([string]$ProfilePath, [string]$Folder) {
    # Run as SYSTEM, the uninstaller can't reach the user's Apps list entry, so remove it here.
    $sid = Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList' |
        Where-Object { $_.GetValue('ProfileImagePath') -eq $ProfilePath } |
        Select-Object -ExpandProperty PSChildName -First 1
    if (-not $sid) { return }
    $hive = "Registry::HKEY_USERS\$sid"
    $loaded = $false
    if (-not (Test-Path $hive)) {  # signed out: load their registry for a moment
        if ($WhatIfPreference) { Write-Output "  Would load $ProfilePath's registry to remove its Apps entry."; return }
        $hive = 'Registry::HKEY_USERS\CasparControllerV1'
        & reg.exe load 'HKU\CasparControllerV1' (Join-Path $ProfilePath 'NTUSER.DAT') | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Output "  Couldn't open $ProfilePath's registry: its Apps entry stays."; return }
        $loaded = $true
    }
    try {
        $key = Join-Path $hive $V1UninstallKey
        # Only v1.x's own entry: v2 may share the AppId (#11).
        if ((Test-Path $key) -and ((Get-ItemProperty $key).InstallLocation -like "$Folder*")) {
            if ($PSCmdlet.ShouldProcess($key, 'Remove the Apps list entry')) { Remove-Item $key -Recurse }
        }
    } finally {
        if ($loaded) { [gc]::Collect(); & reg.exe unload 'HKU\CasparControllerV1' | Out-Null }
    }
}

$failed = $false
foreach ($user in Get-ChildItem -LiteralPath $UsersDir -Directory -ErrorAction SilentlyContinue) {
    $folder = Join-Path $user.FullName $V1Folder
    if (-not (Test-Path -LiteralPath $folder)) { continue }
    if (Test-Path -LiteralPath (Join-Path $folder 'CasparController.exe')) {
        Write-Output "Skipped $folder`: v2 is installed there."
        continue
    }
    Write-Output "v1.x found in $folder"
    Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($V1Exe)) -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$folder\*" } |
        ForEach-Object { if ($PSCmdlet.ShouldProcess("$($_.Path) (pid $($_.Id))", 'Stop')) { $_ | Stop-Process -Force } }
    $uninstaller = Get-ChildItem -LiteralPath $folder -Filter 'unins*.exe' | Sort-Object Name | Select-Object -Last 1
    if (-not $uninstaller) {
        Write-Output "  No uninstaller in $folder`: remove it by hand."
        $failed = $true
        continue
    }
    if ($PSCmdlet.ShouldProcess($uninstaller.FullName, 'Uninstall silently')) {
        $run = Start-Process -FilePath $uninstaller.FullName -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
        if ($run.ExitCode -ne 0) {
            Write-Output "  The uninstaller failed (exit code $($run.ExitCode))."
            $failed = $true
            continue
        }
        Write-Output '  Uninstalled.'
    }
    Remove-UninstallEntry $user.FullName $folder
}
if ($failed) { exit 1 }
exit 0

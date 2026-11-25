param(
    [Parameter(Mandatory = $true)]
    [string]$CurrentExecutable,
    [Parameter(Mandatory = $true)]
    [string]$NewExecutable,
    [string]$NewLauncher = "",
    [switch]$SkipLaunch
)

$ErrorActionPreference = "Stop"
$current = [System.IO.Path]::GetFullPath($CurrentExecutable)
$staged = [System.IO.Path]::GetFullPath($NewExecutable)
$launcher = if ($NewLauncher) {
    Join-Path (Split-Path -Parent $current) "app-launcher\app-launcher.exe"
} else {
    ""
}
$currentBackup = "$current.old"
$launcherBackup = if ($launcher) { "$launcher.old" } else { "" }
$logPath = Join-Path ([System.IO.Path]::GetTempPath()) "ore-filer-update.log"
$currentRenamed = $false
$launcherRenamed = $false
$launcherChanged = $false

function Write-UpdateLog([string]$Message) {
    try {
        Add-Content -LiteralPath $logPath -Encoding UTF8 -Value (
            "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
        )
    }
    catch {
    }
}

function Start-OreFiler {
    if ($SkipLaunch) {
        return
    }
    try {
        Start-Process -FilePath $current -WorkingDirectory (Split-Path -Parent $current)
    }
    catch {
        Write-UpdateLog ("failed to restart: {0}" -f $_.Exception.Message)
    }
}

function Restore-PreviousFiles {
    if ($launcherChanged) {
        try {
            if (Test-Path -LiteralPath $launcher) {
                Remove-Item -LiteralPath $launcher -Force
            }
            if ($launcherRenamed) {
                Rename-Item -LiteralPath $launcherBackup -NewName (
                    [System.IO.Path]::GetFileName($launcher)
                )
            }
        }
        catch {
            Write-UpdateLog ("failed to restore launcher: {0}" -f $_.Exception.Message)
        }
    }
    if ($currentRenamed) {
        try {
            if (Test-Path -LiteralPath $current) {
                Remove-Item -LiteralPath $current -Force
            }
            Rename-Item -LiteralPath $currentBackup -NewName (
                [System.IO.Path]::GetFileName($current)
            )
        }
        catch {
            Write-UpdateLog ("failed to restore executable: {0}" -f $_.Exception.Message)
        }
    }
}

try {
    Write-UpdateLog ("update started: current={0}" -f $current)
    $parentPid = [int]$env:ORE_FILER_PARENT_PID
    if ($parentPid -le 0) {
        throw "ORE_FILER_PARENT_PID is missing or invalid"
    }
    while (Get-Process -Id $parentPid -ErrorAction SilentlyContinue) {
        Start-Sleep -Milliseconds 200
    }

    if (-not (Test-Path -LiteralPath $staged -PathType Leaf)) {
        throw "new executable is missing: $staged"
    }
    if ($NewLauncher -and -not (Test-Path -LiteralPath $NewLauncher -PathType Leaf)) {
        throw "new app-launcher executable is missing: $NewLauncher"
    }

    if ($launcher) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $launcher) | Out-Null
        if (Test-Path -LiteralPath $launcherBackup) {
            Remove-Item -LiteralPath $launcherBackup -Force
        }
        if (Test-Path -LiteralPath $launcher) {
            Rename-Item -LiteralPath $launcher -NewName (
                [System.IO.Path]::GetFileName($launcherBackup)
            )
            $launcherRenamed = $true
        }
        Copy-Item -LiteralPath $NewLauncher -Destination $launcher
        $launcherChanged = $true
    }

    if (Test-Path -LiteralPath $currentBackup) {
        Remove-Item -LiteralPath $currentBackup -Force
    }
    Rename-Item -LiteralPath $current -NewName (
        [System.IO.Path]::GetFileName($currentBackup)
    )
    $currentRenamed = $true
    Copy-Item -LiteralPath $staged -Destination $current

    Remove-Item -LiteralPath $currentBackup -Force -ErrorAction SilentlyContinue
    if ($launcherBackup) {
        Remove-Item -LiteralPath $launcherBackup -Force -ErrorAction SilentlyContinue
    }
    Write-UpdateLog "update completed"
    Start-OreFiler
    Remove-Item -LiteralPath (Split-Path -Parent $staged) -Recurse -Force -ErrorAction SilentlyContinue
}
catch {
    Write-UpdateLog ("update failed: {0}" -f $_.Exception.Message)
    Restore-PreviousFiles
    Start-OreFiler
    exit 1
}

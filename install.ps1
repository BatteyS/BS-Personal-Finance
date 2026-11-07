<#
.SYNOPSIS
Personal Finance, set up on Windows in one step. When it finishes, Personal Finance is in the
Start menu and on your Desktop: click it to open the page.

.DESCRIPTION
Paste this line into PowerShell (in the Start menu, type PowerShell) and press Enter:

  powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/BatteyS/BS-Personal-Finance/main/install.ps1 | iex"

In a copy you already have, run it from that folder instead:

  powershell -ExecutionPolicy Bypass -File install.ps1

It installs only what is missing, all of it for you alone, and needs no administrator rights.
It is install.sh's counterpart, step for step. In order:

  1. git: Git for Windows from winget, for this user only, or where winget can't, MinGit (the
     small git for programs) from GitHub into %LOCALAPPDATA%\Programs\MinGit.
  2. uv, which runs the Python this tracker is pinned to.
  3. Your copy, in %USERPROFILE%\personal-finance, and its Python packages. A copy that's
     already there is brought up to date, unless it has changes of yours.
  4. Personal Finance in the Start menu and on your Desktop, which starts the page and opens it
     (launch.pyw, with no console window). The link bs-personal-finance://launch, which the
     Launch button on seanbattey.com uses, does the same.
  5. Personal Finance on your phone is set up from a Mac for now, so here it is skipped.

Run it again whenever you like: what is done is skipped, your copy is brought up to the newest
version, and the shortcuts and the link are made again, which is how to bring them back or point
them at a copy you moved. Everything it runs prints into
%LOCALAPPDATA%\Personal Finance\Personal Finance install.log.

.PARAMETER Dir
Where the copy goes (default %USERPROFILE%\personal-finance). Run from inside a copy, that copy
is used.

.PARAMETER NoOpen
Don't open the page at the end.

.EXAMPLE
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/BatteyS/BS-Personal-Finance/main/install.ps1))) -Dir D:\personal-finance

The one-line install with an option, pasted into PowerShell.
#>
[CmdletBinding()]
param(
    [string]$Dir = '',
    [switch]$NoOpen
)

# This file stays ASCII: Windows PowerShell 5.1 reads a script file with no byte-order mark as the
# PC's own code page.

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Windows PowerShell's progress bar slows downloads to a crawl
$env:GIT_TERMINAL_PROMPT = '0'             # never stop to ask for a sign-in

$RepoUrl = if ($env:PF_REPO_URL) { $env:PF_REPO_URL } else { 'https://github.com/BatteyS/BS-Personal-Finance.git' }
$ReadmeUrl = 'https://github.com/BatteyS/BS-Personal-Finance#get-started'
$AppName = 'Personal Finance'
$UrlScheme = 'bs-personal-finance'   # the shortcuts' command also answers bs-personal-finance://launch
$LinkKey = "HKCU:\Software\Classes\$UrlScheme"
$LocalAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }
# Windows's own paths are written out, not joined: Join-Path refuses a drive this PC lacks, as a
# test's C:\ on a Mac.
$AppData = "$LocalAppData\$AppName"   # the logs and the icon, as ~/Library/Logs holds the Mac's
$Log = "$AppData\Personal Finance install.log"
$Icon = "$AppData\Personal Finance.ico"
$MinGit = "$LocalAppData\Programs\MinGit"
$Desktop = if ($env:PF_DESKTOP) { $env:PF_DESKTOP } else { [Environment]::GetFolderPath('Desktop') }
$StartMenu = if ($env:PF_START_MENU) { $env:PF_START_MENU } else { [Environment]::GetFolderPath('Programs') }

$Git = $null
$Uv = $null
$Updated = $false
$StopMarker = 'The Personal Finance install stopped.'   # what Stop-Install throws, once it has said why

# Started for this alone (-File, or -c with the one line), the script's exit is the process's.
# Pasted into a PowerShell window of yours, it returns instead, so the window stays open.
$OwnProcess = [bool]$PSCommandPath -or
    [bool]([Environment]::GetCommandLineArgs() | Where-Object { $_ -match '^[-/](c|command|e|ec|encodedcommand|f|file)$' })

# ------------------------------------------------------------------ output

function Write-Step([string]$Text) { Write-Host ''; Write-Host $Text -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host '  ' -NoNewline; Write-Host 'ok' -ForegroundColor Green -NoNewline; Write-Host "    $Text" }
function Write-Note([string]$Text) { Write-Host "        $Text" }

function Write-InstallLog([string[]]$Lines) {
    if ($script:Log -and $Lines) { Add-Content -LiteralPath $script:Log -Value $Lines -Encoding UTF8 }
}

function Show-LogTail {
    if (-not ($script:Log -and (Test-Path -LiteralPath $script:Log))) { return }
    Write-Host ''
    Write-Host "  The end of $($script:Log):"
    Get-Content -LiteralPath $script:Log -Tail 15 | ForEach-Object { Write-Host "    $_" }
}

# Stops the install, saying why and what to do. The lines after the first are shown as they are.
function Stop-Install([string[]]$Message) {
    Write-Host ''
    Write-Host 'Stopped: ' -ForegroundColor Red -NoNewline
    Write-Host $Message[0]
    $Message | Select-Object -Skip 1 | ForEach-Object { Write-Host $_ }
    Write-Host ''
    Write-Host 'Running the same command again picks up where this stopped.'
    throw [System.OperationCanceledException]::new($StopMarker)
}

# Runs a program with what it prints going to the log, and says whether it succeeded. Windows
# PowerShell turns a program's stderr into errors, which would stop this script, so they're let be.
function Invoke-Logged([string]$FilePath, [string[]]$ArgumentList = @()) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 1
    try {
        Write-InstallLog "> $FilePath $($ArgumentList -join ' ')"
        $output = & $FilePath @ArgumentList 2>&1 | ForEach-Object { "$_" }
        Write-InstallLog $output
        return ($LASTEXITCODE -eq 0)
    } catch {
        Write-InstallLog "$_"
        return $false
    } finally {
        $ErrorActionPreference = $saved
    }
}

# What a program prints on stdout, trimmed, or $null when it fails.
function Get-Output([string]$FilePath, [string[]]$ArgumentList = @()) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 1
    try {
        $output = & $FilePath @ArgumentList 2>$null
        if ($LASTEXITCODE -eq 0) { return ((@($output) -join "`n").Trim()) }
    } catch {
    } finally {
        $ErrorActionPreference = $saved
    }
    return $null
}

# The first of these files that exists.
function Find-File([string[]]$Candidates) {
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { return $candidate }
    }
    return $null
}

function Find-Program([string]$Name) {
    $found = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { return $found.Source }
    return $null
}

# Programs this run installs aren't on its PATH until a new window opens: add their folder now.
function Add-ToPath([string]$Folder) {
    if (($env:Path -split ';') -notcontains $Folder) { $env:Path = "$Folder;$env:Path" }
}

# ------------------------------------------------------------------ checks

function Get-Architecture {
    $machine = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($machine) { 'ARM64' { 'arm64' } 'x86' { 'x86' } default { 'x64' } }
}

function Invoke-Preflight {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        Stop-Install "This installer is for Windows. On a Mac, use install.sh, the line under On a Mac in Get started: $ReadmeUrl"
    }
    $version = [Environment]::OSVersion.Version
    if ($version.Major -lt 10) {
        Stop-Install "This PC has Windows $version; Personal Finance needs Windows 10 or 11."
    }
    $windows = if ($version.Build -ge 22000) { 'Windows 11' } else { 'Windows 10' }
    $about = "$windows (build $($version.Build)), $(Get-Architecture), PowerShell $($PSVersionTable.PSVersion)"
    try {
        New-Item -ItemType Directory -Force -Path $AppData | Out-Null
        Set-Content -LiteralPath $Log -Value "--- install.ps1, $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $about" -Encoding UTF8
    } catch {
        $script:Log = $null
    }
    Write-Ok $about
    $principal = New-Object Security.Principal.WindowsPrincipal ([Security.Principal.WindowsIdentity]::GetCurrent())
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Note "This PowerShell runs as administrator, which isn't needed: everything goes into your own"
        Write-Note "user folders. A plain PowerShell window does the same."
    }
    # Windows PowerShell on an older Windows 10 may not offer TLS 1.2, which GitHub needs.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
}

# ----------------------------------------------------------------------- git

function Find-Git {
    Find-File @(
        (Find-Program 'git.exe'),
        "$LocalAppData\Programs\Git\cmd\git.exe",   # Git for Windows, for this user (winget --scope user)
        $(if ($env:ProgramFiles) { "$env:ProgramFiles\Git\cmd\git.exe" }),
        $(if (${env:ProgramFiles(x86)}) { "${env:ProgramFiles(x86)}\Git\cmd\git.exe" }),
        "$MinGit\cmd\git.exe"
    )
}

# MinGit, the small git Git for Windows makes for programs to use: a zip, so no installer and no
# administrator. launch.pyw knows where it is.
function Install-MinGit {
    $name = @{ x64 = '64-bit'; arm64 = 'arm64'; x86 = '32-bit' }[(Get-Architecture)]
    try {
        $release = Invoke-RestMethod -UseBasicParsing -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest'
        $asset = $release.assets | Where-Object { $_.name -match "^MinGit-[\d.]+-$name\.zip$" } | Select-Object -First 1
        if (-not $asset) { Write-InstallLog "No MinGit for $name in $($release.tag_name)."; return }
        $zip = Join-Path ([IO.Path]::GetTempPath()) $asset.name
        Write-InstallLog "Downloading $($asset.browser_download_url)"
        Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $zip
        if (Test-Path -LiteralPath $MinGit) { Remove-Item -LiteralPath $MinGit -Recurse -Force }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($zip, $MinGit)
        Remove-Item -LiteralPath $zip -Force
        Write-InstallLog "MinGit $($release.tag_name) is in $MinGit"
    } catch {
        Write-InstallLog "MinGit didn't install: $_"
    }
}

function Install-Git {
    Write-Step '1. git'
    $script:Git = Find-Git
    if (-not $script:Git) {
        $winget = Find-Program 'winget.exe'
        if ($winget) {
            Write-Note 'Installing Git for Windows with winget, for you alone (a minute or two)...'
            $null = Invoke-Logged $winget @('install', '--id', 'Git.Git', '--exact', '--source', 'winget',
                '--scope', 'user', '--silent', '--accept-package-agreements', '--accept-source-agreements',
                '--disable-interactivity')
            $script:Git = Find-Git
        }
        if (-not $script:Git) {
            Write-Note 'Downloading MinGit, the small git for programs, from GitHub...'
            Install-MinGit
            $script:Git = Find-Git
        }
        if (-not $script:Git) {
            Show-LogTail
            Stop-Install "git didn't install: see above. Install Git for Windows from https://git-scm.com/download/win, then run this again."
        }
    }
    Add-ToPath (Split-Path -Parent $script:Git)
    Write-Ok "$(Get-Output $script:Git @('--version')) at $($script:Git)"
}

# ---------------------------------------------------------------------- uv

function Find-Uv {
    Find-File @(
        (Find-Program 'uv.exe'),
        "$HOME\.local\bin\uv.exe",   # where Astral's installer puts it
        $(if ($env:XDG_BIN_HOME) { "$env:XDG_BIN_HOME\uv.exe" }),
        "$HOME\.cargo\bin\uv.exe"
    )
}

function Install-Uv {
    Write-Step '2. uv, which runs Python'
    $script:Uv = Find-Uv
    if (-not $script:Uv) {
        Write-Note 'Installing uv from astral.sh...'
        # Astral's own installer, in a PowerShell of its own so its exit can't end this one.
        $shell = (Get-Process -Id $PID).Path
        $null = Invoke-Logged $shell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
            'irm https://astral.sh/uv/install.ps1 | iex')
        $script:Uv = Find-Uv
        if (-not $script:Uv) { Show-LogTail; Stop-Install "uv didn't install: see above." }
    }
    Add-ToPath (Split-Path -Parent $script:Uv)
    Write-Ok "$(Get-Output $script:Uv @('--version')) at $($script:Uv)"
}

# ---------------------------------------------------------------- the copy

# A copy of this tracker: a folder with the dashboard and the launchers in it.
function Test-Copy([string]$Folder) {
    (Test-Path -LiteralPath (Join-Path $Folder 'dashboard.py')) -and (Test-Path -LiteralPath (Join-Path $Folder 'launch.sh'))
}

# Bring a copy that's already there up to date: a fast-forward of main, and only when it's on main
# with no changes of yours, so nothing of yours is ever written over (updates.py does the same).
function Update-Copy {
    if (-not (Test-Path -LiteralPath (Join-Path $Dir '.git'))) { return }
    $branch = Get-Output $Git @('-C', $Dir, 'rev-parse', '--abbrev-ref', 'HEAD')
    if ($branch -ne 'main') {
        Write-Note "It's on the branch $branch, not main, so it wasn't updated."
        return
    }
    if (Get-Output $Git @('-C', $Dir, 'status', '--porcelain', '--untracked-files=no')) {
        Write-Note "It has changes of yours to its files, so it wasn't updated."
        return
    }
    $before = Get-Output $Git @('-C', $Dir, 'rev-parse', 'HEAD')
    if (-not (Invoke-Logged $Git @('-C', $Dir, 'pull', '--ff-only', '--quiet', 'origin', 'main'))) {
        Write-Note "It couldn't be brought up to date just now (see $Log); it works as it is."
        return
    }
    $subject = Get-Output $Git @('-C', $Dir, 'log', '-1', '--format=%s')
    if ((Get-Output $Git @('-C', $Dir, 'rev-parse', 'HEAD')) -eq $before) {
        Write-Ok "already up to date: $subject"
    } else {
        $script:Updated = $true
        Write-Ok "brought up to date: $subject"
    }
}

# A page started before the update still runs the old code: stop it, and the next start is new.
function Restart-Page {
    if (-not $Updated) { return }
    $python = Join-Path $Dir '.venv\Scripts\python.exe'
    $launcher = Join-Path $Dir 'launch.pyw'
    if ((Test-Path -LiteralPath $python) -and (Test-Path -LiteralPath $launcher)) {
        $null = Invoke-Logged $python @($launcher, '--stop')
    }
}

# Whether a folder is inside OneDrive, or in Documents or on the Desktop, which OneDrive often keeps.
function Test-Synced([string]$Folder) {
    $roots = @($env:OneDrive, $env:OneDriveConsumer, $env:OneDriveCommercial,
        [Environment]::GetFolderPath('MyDocuments'), [Environment]::GetFolderPath('Desktop')) | Where-Object { $_ }
    foreach ($root in $roots) {
        if ($Folder.StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Install-Copy {
    Write-Step '3. Your copy'
    if ((Test-Path -LiteralPath $Dir) -and -not (Test-Copy $Dir)) {
        Stop-Install @("$Dir is already there and isn't a copy of Personal Finance. Move it, or name another folder with -Dir:",
            '', "  & ([scriptblock]::Create((irm https://raw.githubusercontent.com/BatteyS/BS-Personal-Finance/main/install.ps1))) -Dir $HOME\some-other-folder")
    }
    if (-not (Test-Path -LiteralPath $Dir)) {
        if (-not (Invoke-Logged $Git @('clone', '--quiet', $RepoUrl, $Dir))) {
            Show-LogTail
            Stop-Install "Couldn't download Personal Finance from $($RepoUrl): see above."
        }
        Write-Ok "made $Dir"
    } else {
        Write-Ok $Dir
        Update-Copy
    }
    if (Test-Synced $Dir) {
        Write-Note 'This folder may be kept in OneDrive, which can move files to the cloud and'
        Write-Note "break the Python packages. A folder that isn't synced, like $HOME\personal-finance, is safer."
    }
    Write-Note 'Installing Python and the packages (a minute or two the first time)...'
    if (-not (Invoke-Logged $Uv @('--directory', $Dir, 'sync'))) {
        Show-LogTail
        Stop-Install "The packages didn't install: see above."
    }
    Write-Ok 'packages installed'
    New-Item -ItemType Directory -Force -Path (Join-Path $Dir 'data') | Out-Null
}

# ------------------------------------------------- the shortcuts and the link

# What the shortcuts and the link run: launch.pyw under the copy's pythonw.exe, the Python that
# opens no console window.
function Get-Launcher([string]$Folder) {
    [pscustomobject]@{
        Program   = "$Folder\.venv\Scripts\pythonw.exe"
        Arguments = '"' + "$Folder\launch.pyw" + '"'
        Folder    = $Folder
    }
}

# The command line the link runs: the shortcut's, as one string. What follows the scheme in the
# link is never passed on, as on the Mac.
function Get-LinkCommand($Launcher) {
    '"{0}" {1}' -f $Launcher.Program, $Launcher.Arguments
}

# Whether the shortcut at $Path is a Personal Finance this installer made.
function Test-OurShortcut([string]$Path) {
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($Path)
    ($shortcut.TargetPath -like '*\pythonw.exe') -and ($shortcut.Arguments -like '*launch.pyw*')
}

function New-Shortcut([string]$Path, $Launcher) {
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Force }
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($Path)
    $shortcut.TargetPath = $Launcher.Program
    $shortcut.Arguments = $Launcher.Arguments
    $shortcut.WorkingDirectory = $Launcher.Folder
    $shortcut.Description = 'Opens Personal Finance in your browser'
    if (Test-Path -LiteralPath $Icon) { $shortcut.IconLocation = "$Icon,0" }
    $shortcut.Save()
}

# bs-personal-finance://launch, for this user: the scheme's key under HKCU\Software\Classes, made
# anew each time, with the command the shortcuts run.
function Register-Link($Launcher) {
    if (Test-Path -LiteralPath $LinkKey) { Remove-Item -LiteralPath $LinkKey -Recurse -Force }
    # The command's key first, which makes the scheme's key and shell\open on the way. New-Item
    # -Force on a key that's already there would empty it.
    $null = New-Item -Path "$LinkKey\shell\open\command" -Value (Get-LinkCommand $Launcher) -Force
    Set-ItemProperty -LiteralPath $LinkKey -Name '(default)' -Value "URL:$AppName"
    $null = New-ItemProperty -LiteralPath $LinkKey -Name 'URL Protocol' -Value '' -PropertyType String -Force
    # The name a browser shows when it asks whether to open the link, in place of Python's.
    $null = New-ItemProperty -LiteralPath "$LinkKey\shell\open" -Name 'FriendlyAppName' -Value $AppName -PropertyType String -Force
    if (Test-Path -LiteralPath $Icon) { $null = New-Item -Path "$LinkKey\DefaultIcon" -Value "$Icon,0" -Force }
}

function Install-Shortcuts {
    Write-Step '4. Personal Finance in the Start menu and on your Desktop'
    if (-not (Test-Path -LiteralPath (Join-Path $Dir 'launch.pyw'))) {
        Stop-Install "$Dir has no launch.pyw, which opens the page on Windows: it is older than that. Bring it up to date (in it: git pull), then run this again."
    }
    $launcher = Get-Launcher $Dir
    if (-not (Test-Path -LiteralPath $launcher.Program)) {
        Show-LogTail
        Stop-Install "$($launcher.Program) isn't there, so the packages didn't install as they should: see above."
    }
    $links = @((Join-Path $StartMenu "$AppName.lnk"), (Join-Path $Desktop "$AppName.lnk"))
    foreach ($link in $links) {
        if ((Test-Path -LiteralPath $link) -and -not (Test-OurShortcut $link)) {
            Stop-Install "$(Split-Path -Parent $link) already has something else called $AppName, which stays as it is. Rename it, then run this again."
        }
    }
    # The app's own icon (icon.py), in place of Python's.
    if (-not (Invoke-Logged $Uv @('--directory', $Dir, 'run', '--quiet', 'icon.py', '--ico', $Icon))) {
        Write-Note "The icon couldn't be drawn (see $Log), so the shortcuts show Python's."
    }
    try {
        foreach ($link in $links) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $link) | Out-Null
            New-Shortcut $link $launcher
            Write-Ok $link
        }
        Register-Link $launcher
        Write-Ok "the link $($UrlScheme)://launch"
    } catch {
        Write-InstallLog "$_"
        Stop-Install "Couldn't make Personal Finance's shortcuts: $_"
    }
}

# ------------------------------------------------------------------- the phone

function Skip-Phone {
    Write-Step '5. Personal Finance on your phone'
    Write-Note 'Phone access is set up from a Mac for now (phone.py), so it is skipped here.'
}

# --------------------------------------------------------------------- run

# Run from inside a copy (-File install.ps1), that copy is used.
$here = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { $null }
if (-not $Dir -and $here -and (Test-Copy $here)) { $Dir = $here }
if (-not $Dir) { $Dir = Join-Path $HOME 'personal-finance' }
$Dir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Dir)

$failed = $false
try {
    Write-Step 'Personal Finance'
    Invoke-Preflight
    Install-Git
    Install-Uv
    Install-Copy
    Restart-Page
    Install-Shortcuts
    Skip-Phone

    Write-Step 'Done'
    Write-Note 'Click Personal Finance in the Start menu or on your Desktop whenever you want the page.'
    Write-Note 'It opens in your browser, with no window of its own, and stops by itself twenty minutes'
    Write-Note 'after its last tab closes.'
    if (-not ((Test-Path -LiteralPath (Join-Path $Dir 'data\accounts.toml')) -and (Test-Path -LiteralPath (Join-Path $Dir 'data\budget.toml')))) {
        Write-Note 'Until you set it up, it shows example accounts and an example budget: see Set it up in'
        Write-Note "$Dir\README.md."
    }
    if (-not $NoOpen) {
        Write-Note 'Opening the page...'
        $ErrorActionPreference = 'Continue'   # what the launcher says on stderr is for the reader
        $global:LASTEXITCODE = 1
        & (Join-Path $Dir '.venv\Scripts\python.exe') (Join-Path $Dir 'launch.pyw')
        $opened = $LASTEXITCODE -eq 0
        $ErrorActionPreference = 'Stop'
        if (-not $opened) { Stop-Install "The page didn't open: see the message above." }
    }
} catch {
    $failed = $true
    if ($_.Exception.Message -ne $StopMarker) {
        # Something this script didn't foresee: say what, and where the rest is.
        Write-InstallLog @("$_", $_.ScriptStackTrace)
        try { Stop-Install @("$_", "What it ran is in $Log.") } catch { }
    }
}
if ($failed -and $OwnProcess) { exit 1 }

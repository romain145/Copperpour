<#
.SYNOPSIS
Opens and closes KiCad 10 for an agent working alone (Windows only): a board in the PCB editor,
or a whole project (project manager, schematic editor and PCB editor).

.DESCRIPTION
open -Board x.kicad_pcb
        Launches pcbnew on the board and waits until its window is up, no dialog is showing and
        KiCad's IPC pipe exists.
open -Project x.kicad_pro
        Marks x.kicad_sch and x.kicad_pcb as open in x.kicad_prl, launches kicad.exe on the
        project and waits until KiCad's session restore has opened both editors. Needs KiCad's
        "Remember open files for next project launch" (Preferences > Common).
        Both refuse while any other KiCad runs: the MCP server would then see two IPC endpoints.
close   Closes what this script opened, and only that. Unsaved changes (an editor title starting
        with '*') or a dialog on screen are refused unless -Force. Otherwise it closes the PCB
        editor (board mode) or the project manager (project mode) as a user would, and waits
        for KiCad to exit. With -Force, when something is unsaved, a dialog is up, or the close
        does not finish, it ends the process (unsaved changes are lost) and removes the lock
        files it left.
status  Lists running KiCad processes and their windows, this script's sessions, the target's
        lock files and KiCad's IPC pipes.

Output is one JSON object on stdout. Exit codes: 0 done; 3 refused before doing anything;
4 blocked (unsaved changes, a dialog, an early exit or a timeout; KiCad is left as it is);
1 unexpected error.

.EXAMPLE
pwsh -File session.ps1 open  -Board C:\work\amp\amp.kicad_pcb
pwsh -File session.ps1 open  -Project C:\work\amp\amp.kicad_pro
pwsh -File session.ps1 close -Project C:\work\amp\amp.kicad_pro -Force
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)][ValidateSet('open', 'close', 'status')][string]$Action,
    [string]$Board,
    [string]$Project,
    [switch]$Force,
    [int]$TimeoutSeconds,
    [string]$KiCadBin = 'C:\Program Files\KiCad\10.0\bin',
    [switch]$AllowOtherKiCad
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3

if (-not ('KiCadSessionWin' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class KiCadSessionWin
{
    public delegate bool EnumProc(IntPtr handle, IntPtr param);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc callback, IntPtr param);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr parent, EnumProc callback, IntPtr param);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr handle, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr handle, StringBuilder text, int max);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr handle, StringBuilder text, int max);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr handle);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr handle, uint message, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr handle, uint command);

    // A dialog has an owner window; an editor frame has none.
    public static bool HasOwner(IntPtr handle) { return GetWindow(handle, 4 /* GW_OWNER */) != IntPtr.Zero; }
    public static string TextOf(IntPtr handle) { var s = new StringBuilder(1024); GetWindowText(handle, s, s.Capacity); return s.ToString(); }
    public static string ClassOf(IntPtr handle) { var s = new StringBuilder(256); GetClassName(handle, s, s.Capacity); return s.ToString(); }

    public static List<IntPtr> TopLevel(uint pid)
    {
        var found = new List<IntPtr>();
        EnumWindows((h, p) => { uint owner; GetWindowThreadProcessId(h, out owner); if (owner == pid && IsWindowVisible(h)) found.Add(h); return true; }, IntPtr.Zero);
        return found;
    }

    public static List<string> ChildTexts(IntPtr parent)
    {
        var found = new List<string>();
        EnumChildWindows(parent, (h, p) => { if (IsWindowVisible(h)) { var t = TextOf(h); if (t.Trim().Length > 0) found.Add(t.Trim()); } return true; }, IntPtr.Zero);
        return found;
    }
}
'@
}

$WM_CLOSE = 0x0010
$StateDir = Join-Path $env:LOCALAPPDATA 'kicad-session'

function Out-Result([System.Collections.IDictionary]$Body, [int]$Code) {
    [pscustomobject]$Body | ConvertTo-Json -Depth 6
    exit $Code
}

function Refuse([string]$Reason, [string]$Message, [System.Collections.IDictionary]$Extra = @{}) {
    $body = [ordered]@{ status = 'refused'; reason = $Reason }
    foreach ($k in $Extra.Keys) { $body[$k] = $Extra[$k] }
    $body['message'] = $Message
    Out-Result $body 3
}

# What to open or close: a board alone (pcbnew), or a project with its schematic and board
# (kicad.exe). 'path' is what the launched process gets on its command line.
function Resolve-Target {
    if ($Board -and $Project) { Refuse 'board_and_project' 'Pass -Board or -Project, not both.' }
    if (-not $Board -and -not $Project) { Refuse 'missing_target' 'Pass -Board <x.kicad_pcb> or -Project <x.kicad_pro>.' }
    $given = if ($Project) { $Project } else { $Board }
    $full = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($given)
    $want = if ($Project) { '.kicad_pro' } else { '.kicad_pcb' }
    if ([IO.Path]::GetExtension($full) -ne $want) { Refuse 'wrong_file_type' "Expected a $want file." @{ path = $full } }
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { Refuse 'file_not_found' 'No such file.' @{ path = $full } }
    $boardPath = [IO.Path]::ChangeExtension($full, '.kicad_pcb')
    $schPath = [IO.Path]::ChangeExtension($full, '.kicad_sch')
    if ($Project) {
        $missing = @($boardPath, $schPath | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
        if ($missing) { Refuse 'project_files_missing' 'Project mode opens the project''s schematic and board; these are missing.' @{ missing = $missing } }
    }
    [pscustomobject]@{
        mode = if ($Project) { 'project' } else { 'board' }
        path = $full
        board = $boardPath
        schematic = if ($Project) { $schPath } else { $null }
        project = [IO.Path]::ChangeExtension($full, '.kicad_pro')
        base = [IO.Path]::GetFileNameWithoutExtension($full)
        dir = [IO.Path]::GetDirectoryName($full)
        exe = Join-Path $KiCadBin $(if ($Project) { 'kicad.exe' } else { 'pcbnew.exe' })
        # Frames to wait for: the PCB editor; or the project manager and both editors.
        frames_expected = if ($Project) { 3 } else { 1 }
    }
}

function Get-StatePath($Target) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Target.path.ToLowerInvariant())
    $hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).Substring(0, 16)
    Join-Path $StateDir "$hash.json"
}

function Read-Session([string]$StatePath) {
    if (-not (Test-Path -LiteralPath $StatePath)) { return $null }
    try { Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json } catch { $null }
}

function Write-Session([string]$StatePath, [System.Collections.IDictionary]$Session) {
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $Session | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8NoBOM
}

function Get-KiCadProcesses {
    @(Get-CimInstance Win32_Process -Filter "Name = 'kicad.exe' OR Name = 'pcbnew.exe' OR Name = 'eeschema.exe'" |
            ForEach-Object {
                [pscustomobject]@{ pid = [int]$_.ProcessId; name = $_.Name; path = $_.ExecutablePath; command_line = $_.CommandLine }
            })
}

# The process is the one this script launched for this target: the expected KiCad executable,
# with the target's path on its command line. Guards against a PID reused by another program.
function Test-Owned($Process, $Target) {
    [bool]($Process -and $Process.path -and $Process.command_line -and
        $Process.path -ieq $Target.exe -and
        $Process.command_line.IndexOf($Target.path, [StringComparison]::OrdinalIgnoreCase) -ge 0)
}

# Visible top-level windows of a process. Frames have no owner window; the project manager's and
# each editor's title names the project once it is loaded, and '*' before an editor's title marks
# unsaved changes. Owned windows are dialogs: a question KiCad is asking, or a progress window.
function Get-WindowState([int]$ProcessId, $Target) {
    $frames = @()
    $otherFrames = @()
    $dialogs = @()
    foreach ($h in [KiCadSessionWin]::TopLevel([uint32]$ProcessId)) {
        $class = [KiCadSessionWin]::ClassOf($h)
        $title = [KiCadSessionWin]::TextOf($h)
        if ([KiCadSessionWin]::HasOwner($h) -or $class -eq '#32770') {
            if ($title.Trim() -or $class -eq '#32770') {
                $dialogs += [pscustomobject]@{ title = $title; class = $class; text = (([KiCadSessionWin]::ChildTexts($h)) -join ' | ') }
            }
        }
        elseif ($title.IndexOf($Target.base, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $frames += [pscustomobject]@{ handle = [int64]$h; title = $title }
        }
        elseif ($title.Trim()) { $otherFrames += $title }
    }
    $dirty = @($frames | Where-Object { $_.title.StartsWith('*') } | ForEach-Object title)
    [pscustomobject]@{
        frames = $frames
        frame_titles = @($frames | ForEach-Object title)
        dirty = $dirty.Count -gt 0
        dirty_frames = $dirty
        dialogs = $dialogs
        other_frames = $otherFrames
    }
}

# The window whose close ends the session: the PCB editor in board mode, the project manager in
# project mode (closing it closes both editors and keeps them listed as open for the next launch).
function Get-CloseWindow($Windows, $Target, $Session) {
    if ($Target.mode -eq 'board') { return $Windows.frames | Select-Object -First 1 }
    if ($Session -and $Session.PSObject.Properties['manager_handle'] -and $Session.manager_handle) {
        $known = $Windows.frames | Where-Object handle -EQ $Session.manager_handle | Select-Object -First 1
        if ($known) { return $known }
    }
    # The project manager's title is '<project> — KiCad <version>' in every UI language.
    $Windows.frames | Where-Object { $_.title -match 'KiCad \d' } | Select-Object -First 1
}

function Get-ApiPipes {
    @([IO.Directory]::GetFiles('\\.\pipe\') | Where-Object { $_ -match '(?i)kicad.*api[^\\]*\.sock$' })
}

# KiCad's lock files for the target: '~<file>.lck', JSON naming user and host. Board mode: the
# board and its project. Project mode: the project, its board and every schematic sheet beside it.
function Get-Locks($Target) {
    $names = @([IO.Path]::GetFileName($Target.board), [IO.Path]::GetFileName($Target.project))
    if ($Target.mode -eq 'project') {
        $names += @(Get-ChildItem -LiteralPath $Target.dir -Filter '*.kicad_sch' -File | ForEach-Object Name)
    }
    @(foreach ($name in $names | Select-Object -Unique) {
            $path = Join-Path $Target.dir "~$name.lck"
            if (Test-Path -LiteralPath $path) {
                $owner = $null
                try { $owner = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json } catch { }
                [pscustomobject]@{
                    path = $path
                    username = if ($owner -and $owner.PSObject.Properties['username']) { $owner.username } else { $null }
                    hostname = if ($owner -and $owner.PSObject.Properties['hostname']) { $owner.hostname } else { $null }
                }
            }
        })
}

# Called only once the process this script launched has exited and no KiCad runs at all, so no
# live KiCad can own these locks. Only locks naming this user on this machine are removed.
function Remove-OwnStaleLocks($Target) {
    @(foreach ($lock in Get-Locks $Target) {
            if ($lock.username -ieq $env:USERNAME -and $lock.hostname -ieq $env:COMPUTERNAME) {
                Remove-Item -LiteralPath $lock.path
                $lock.path
            }
        })
}

# The KiCad settings this launch will use: KICAD_CONFIG_HOME\10.0 when set, as KiCad does.
function Get-CommonSettings {
    $dir = if ($env:KICAD_CONFIG_HOME) { Join-Path $env:KICAD_CONFIG_HOME '10.0' } else { Join-Path $env:APPDATA 'kicad\10.0' }
    $file = Join-Path $dir 'kicad_common.json'
    if (-not (Test-Path -LiteralPath $file)) { return $null }
    try { $j = Get-Content -LiteralPath $file -Raw | ConvertFrom-Json } catch { return $null }
    [pscustomobject]@{
        file = $file
        api_server = [bool]($j.PSObject.Properties['api'] -and $j.api.PSObject.Properties['enable_server'] -and $j.api.enable_server)
        remember_open_files = [bool]($j.PSObject.Properties['session'] -and $j.session.PSObject.Properties['remember_open_files'] -and $j.session.remember_open_files)
    }
}

# Session restore reopens the files .kicad_prl lists as open (KICAD_MANAGER_FRAME::OnIdle reads
# only each entry's name and 'open'). Marks the schematic and board open, leaving the rest as is.
function Set-RestoreEntries($Target) {
    $prl = [IO.Path]::ChangeExtension($Target.project, '.kicad_prl')
    $wanted = @([IO.Path]::GetFileName($Target.schematic), [IO.Path]::GetFileName($Target.board))
    if (Test-Path -LiteralPath $prl) {
        $json = Get-Content -LiteralPath $prl -Raw | ConvertFrom-Json -AsHashtable
    }
    else {
        $json = [ordered]@{ meta = [ordered]@{ filename = [IO.Path]::GetFileName($prl); version = 5 } }
    }
    if (-not $json.Contains('project') -or $json['project'] -isnot [System.Collections.IDictionary]) { $json['project'] = [ordered]@{} }
    $files = [System.Collections.ArrayList]@()
    if ($json['project'].Contains('files') -and $json['project']['files']) { $files.AddRange(@($json['project']['files'])) }
    $changed = @()
    foreach ($name in $wanted) {
        $entry = $files | Where-Object { $_ -is [System.Collections.IDictionary] -and $_['name'] -eq $name } | Select-Object -First 1
        if (-not $entry) {
            [void]$files.Add([ordered]@{ name = $name; open = $true })
            $changed += $name
        }
        elseif ($entry['open'] -ne $true) {
            $entry['open'] = $true
            $changed += $name
        }
    }
    if ($changed) {
        $json['project']['files'] = $files.ToArray()
        $json | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $prl -Encoding utf8NoBOM
    }
    [pscustomobject]@{ prl = $prl; marked_open = $changed }
}

function Invoke-Open {
    $target = Resolve-Target
    $statePath = Get-StatePath $target
    $existing = Read-Session $statePath
    if ($existing) {
        $proc = Get-KiCadProcesses | Where-Object pid -EQ $existing.pid
        if (Test-Owned $proc $target) {
            $windows = Get-WindowState $existing.pid $target
            $ready = $windows.frames.Count -ge $target.frames_expected -and -not $windows.dialogs
            Out-Result ([ordered]@{
                    status = if ($ready) { 'open' } else { 'blocked' }
                    reason = if ($ready) { $null } elseif ($windows.dialogs) { 'dialog_open' } else { 'not_ready' }
                    reused = $true; mode = $target.mode; pid = $existing.pid; target = $target.path
                    windows = $windows.frame_titles; unsaved = $windows.dirty_frames; dialogs = $windows.dialogs
                    message = if ($ready) { 'Already open in a KiCad this script launched.' } else { 'The KiCad this script launched is not ready; see dialogs. `close -Force` ends it.' }
                }) $(if ($ready) { 0 } else { 4 })
        }
        Remove-Item -LiteralPath $statePath
    }

    $running = @(Get-KiCadProcesses)
    if ($running.Count -and -not $AllowOtherKiCad) {
        Refuse 'other_kicad_running' 'Another KiCad is running. The MCP server would see two IPC endpoints. Ask the user to close it; never close a KiCad this script did not open.' @{
            target = $target.path
            running = @($running | ForEach-Object { [ordered]@{ pid = $_.pid; name = $_.name; windows = @((Get-WindowState $_.pid $target).frame_titles + (Get-WindowState $_.pid $target).other_frames) } })
        }
    }
    if (-not (Test-Path -LiteralPath $target.exe)) { Refuse 'kicad_not_found' 'KiCad executable not found; pass -KiCadBin.' @{ exe = $target.exe } }

    $settings = Get-CommonSettings
    if ($settings -and -not $settings.api_server) {
        Refuse 'api_server_off' 'KiCad''s IPC API server is off, so the MCP server could not reach KiCad. Ask the user to enable it: Preferences > Plugins > Enable KiCad API.' @{ settings = $settings.file }
    }
    $restore = $null
    if ($target.mode -eq 'project') {
        if ($settings -and -not $settings.remember_open_files) {
            Refuse 'session_restore_off' 'Project mode needs KiCad to reopen the editors itself. Ask the user to enable Preferences > Common > "Remember open files for next project launch", or use -Board to open the PCB editor alone.' @{ settings = $settings.file }
        }
        $restore = Set-RestoreEntries $target
    }

    $locksBefore = Get-Locks $target
    $timeout = if ($TimeoutSeconds) { $TimeoutSeconds } elseif ($target.mode -eq 'project') { 120 } else { 90 }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process -FilePath $target.exe -ArgumentList "`"$($target.path)`"" -PassThru
    $session = [ordered]@{ pid = $process.Id; mode = $target.mode; target = $target.path; exe = $target.exe; started = (Get-Date).ToString('o'); manager_handle = $null }
    Write-Session $statePath $session

    $readyPolls = 0
    $idleSince = $null
    $lastCpu = [TimeSpan]::Zero
    $lastCpuAt = 0.0
    while ($clock.Elapsed.TotalSeconds -lt $timeout) {
        if ($process.HasExited) {
            Remove-Item -LiteralPath $statePath -ErrorAction SilentlyContinue
            Out-Result ([ordered]@{
                    status = 'failed'; reason = 'exited_during_startup'; mode = $target.mode; target = $target.path; pid = $process.Id
                    exit_code = $process.ExitCode; elapsed_ms = $clock.ElapsedMilliseconds
                    message = 'KiCad exited before its windows came up. Retry once; if it exits again, tell the user.'
                }) 4
        }
        $windows = Get-WindowState $process.Id $target

        # The project manager is the first frame to appear; remember it for close.
        if ($target.mode -eq 'project' -and -not $session.manager_handle -and $windows.frames.Count) {
            $manager = Get-CloseWindow $windows $target $null
            $session.manager_handle = if ($manager) { $manager.handle } else { $windows.frames[0].handle }
            Write-Session $statePath $session
        }

        $pipes = @(Get-ApiPipes)
        if ($windows.frames.Count -ge $target.frames_expected -and -not $windows.dialogs -and $pipes) {
            $readyPolls++
            if ($readyPolls -ge 3) {
                Out-Result ([ordered]@{
                        status = 'open'; reused = $false; mode = $target.mode; pid = $process.Id; target = $target.path
                        windows = $windows.frame_titles; unsaved = $windows.dirty_frames
                        ipc_pipes = $pipes; elapsed_ms = $clock.ElapsedMilliseconds
                        marked_open_in_prl = if ($restore) { $restore.marked_open } else { $null }
                        stale_locks_before = @($locksBefore | ForEach-Object path)
                        message = 'Open. Confirm with the MCP server''s get_editor_state that the board is the open document before editing.'
                    }) 0
            }
        }
        else { $readyPolls = 0 }

        # A dialog is up and KiCad has been idle for 5 s: it is waiting for an answer, not
        # loading (a progress window keeps the process busy).
        $process.Refresh()
        $now = $clock.Elapsed.TotalSeconds
        if ($now - $lastCpuAt -ge 2) {
            $busy = ($process.TotalProcessorTime - $lastCpu).TotalSeconds -gt 0.1
            $lastCpu = $process.TotalProcessorTime
            $lastCpuAt = $now
            if ($windows.dialogs -and -not $busy) { if ($null -eq $idleSince) { $idleSince = $now } }
            else { $idleSince = $null }
        }
        if ($null -ne $idleSince -and $now - $idleSince -ge 5) {
            Out-Result ([ordered]@{
                    status = 'blocked'; reason = 'dialog_open'; mode = $target.mode; target = $target.path; pid = $process.Id
                    windows = $windows.frame_titles; dialogs = $windows.dialogs; elapsed_ms = $clock.ElapsedMilliseconds
                    message = 'KiCad is asking a question. Nothing has been edited yet, so `close -Force` is safe; tell the user what it says, and do not answer it for them.'
                }) 4
        }
        Start-Sleep -Milliseconds 500
    }
    $windows = Get-WindowState $process.Id $target
    Out-Result ([ordered]@{
            status = 'blocked'; reason = 'timeout'; mode = $target.mode; target = $target.path; pid = $process.Id
            windows = $windows.frame_titles; dialogs = $windows.dialogs; ipc_pipes = @(Get-ApiPipes); elapsed_ms = $clock.ElapsedMilliseconds
            message = "Not ready after $timeout s. KiCad is left running; ``close -Force`` ends it."
        }) 4
}

function Invoke-Close {
    $target = Resolve-Target
    $statePath = Get-StatePath $target
    $session = Read-Session $statePath
    if (-not $session) {
        Refuse 'not_opened_here' 'This script did not open this, so it does not close it. A KiCad the user opened is theirs to close.' @{
            target = $target.path
            running = @(Get-KiCadProcesses | ForEach-Object { [ordered]@{ pid = $_.pid; name = $_.name } })
        }
    }

    $proc = Get-KiCadProcesses | Where-Object pid -EQ $session.pid
    if (-not $proc) {
        Remove-Item -LiteralPath $statePath
        $removed = if (-not @(Get-KiCadProcesses).Count) { Remove-OwnStaleLocks $target } else { @() }
        Out-Result ([ordered]@{
                status = 'closed'; already_exited = $true; mode = $target.mode; target = $target.path; pid = $session.pid
                removed_stale_locks = $removed; locks_left = @(Get-Locks $target | ForEach-Object path)
                message = 'KiCad had already exited.'
            }) 0
    }
    if (-not (Test-Owned $proc $target)) {
        Remove-Item -LiteralPath $statePath
        Refuse 'process_not_ours' 'The recorded process is no longer the KiCad this script opened; nothing was closed.' @{
            target = $target.path; pid = $session.pid; command_line = $proc.command_line
        }
    }

    $process = Get-Process -Id $session.pid
    $windows = Get-WindowState $session.pid $target
    $closeWindow = Get-CloseWindow $windows $target $session
    $timeout = if ($TimeoutSeconds) { $TimeoutSeconds } else { 30 }
    $clock = [Diagnostics.Stopwatch]::StartNew()

    if (-not $Force -and ($windows.dirty -or $windows.dialogs -or -not $closeWindow)) {
        $reason = if ($windows.dirty) { 'unsaved_changes' } elseif ($windows.dialogs) { 'dialog_open' } else { 'window_not_found' }
        $message = switch ($reason) {
            'unsaved_changes' { 'Nothing was closed. Save the board with the MCP server''s save_project and close again. KiCad 10 cannot save a schematic over IPC: unsaved schematic changes need the user, or -Force to discard them.' }
            'dialog_open' { 'Nothing was closed. KiCad is showing a dialog; tell the user what it says. -Force ends KiCad and loses unsaved changes.' }
            default { 'Nothing was closed: the window to close was not found. -Force ends KiCad and loses unsaved changes.' }
        }
        Out-Result ([ordered]@{
                status = 'blocked'; reason = $reason; mode = $target.mode; target = $target.path; pid = $session.pid
                windows = $windows.frame_titles; unsaved = $windows.dirty_frames; dialogs = $windows.dialogs
                message = $message
            }) 4
    }

    $closeAttempted = $false
    if (-not $windows.dirty -and -not $windows.dialogs -and $closeWindow) {
        $closeAttempted = $true
        [void][KiCadSessionWin]::PostMessage([IntPtr]$closeWindow.handle, $WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero)
        if ($process.WaitForExit($timeout * 1000)) {
            Remove-Item -LiteralPath $statePath
            Out-Result ([ordered]@{
                    status = 'closed'; forced = $false; mode = $target.mode; target = $target.path; pid = $session.pid
                    elapsed_ms = $clock.ElapsedMilliseconds; locks_left = @(Get-Locks $target | ForEach-Object path)
                    message = 'Closed the way a user would.'
                }) 0
        }
        if (-not $Force) {
            $after = Get-WindowState $session.pid $target
            Out-Result ([ordered]@{
                    status = 'blocked'; reason = 'close_did_not_finish'; mode = $target.mode; target = $target.path; pid = $session.pid
                    windows = $after.frame_titles; dialogs = $after.dialogs; elapsed_ms = $clock.ElapsedMilliseconds
                    message = 'KiCad did not exit. Tell the user what the dialog says; -Force ends KiCad and loses unsaved changes.'
                }) 4
        }
    }

    # -Force: end the process. Unsaved changes are lost; KiCad leaves its locks behind.
    $before = Get-WindowState $session.pid $target
    Stop-Process -Id $session.pid -Force
    [void]$process.WaitForExit(10000)
    Remove-Item -LiteralPath $statePath
    $othersRunning = @(Get-KiCadProcesses).Count
    $removed = if (-not $othersRunning) { Remove-OwnStaleLocks $target } else { @() }
    Out-Result ([ordered]@{
            status = 'closed'; forced = $true; mode = $target.mode; target = $target.path; pid = $session.pid
            graceful_close_tried = $closeAttempted
            unsaved_discarded = $before.dirty_frames; dialogs_dismissed = $before.dialogs
            removed_stale_locks = $removed; locks_left = @(Get-Locks $target | ForEach-Object path)
            elapsed_ms = $clock.ElapsedMilliseconds
            message = if ($othersRunning) { 'Ended KiCad. Another KiCad is running, so the locks were left in place.' } else { 'Ended KiCad and removed the locks it left.' }
        }) 0
}

function Invoke-Status {
    $target = if ($Board -or $Project) { Resolve-Target } else { $null }
    $sessions = @(if (Test-Path -LiteralPath $StateDir) {
            Get-ChildItem -LiteralPath $StateDir -Filter '*.json' | ForEach-Object {
                $s = Read-Session $_.FullName
                if ($s) {
                    $proc = Get-KiCadProcesses | Where-Object pid -EQ $s.pid
                    [ordered]@{ pid = $s.pid; mode = $s.mode; target = $s.target; started = $s.started; running = [bool]($proc -and $proc.command_line -and $proc.command_line.IndexOf($s.target, [StringComparison]::OrdinalIgnoreCase) -ge 0) }
                }
            }
        })
    Out-Result ([ordered]@{
            status = 'ok'
            kicad_processes = @(Get-KiCadProcesses | ForEach-Object {
                    $p = $_
                    [ordered]@{
                        pid = $p.pid; name = $p.name; command_line = $p.command_line
                        windows = @([KiCadSessionWin]::TopLevel([uint32]$p.pid) | ForEach-Object {
                                $t = [KiCadSessionWin]::TextOf($_); $c = [KiCadSessionWin]::ClassOf($_)
                                if ($t.Trim() -or $c -eq '#32770') { "$t [$c$(if ([KiCadSessionWin]::HasOwner($_)) { ', dialog' })]" }
                            })
                    }
                })
            sessions = $sessions
            locks = if ($target) { @(Get-Locks $target) } else { $null }
            ipc_pipes = @(Get-ApiPipes)
            settings = Get-CommonSettings
        }) 0
}

try {
    switch ($Action) {
        'open' { Invoke-Open }
        'close' { Invoke-Close }
        'status' { Invoke-Status }
    }
}
catch {
    Out-Result ([ordered]@{ status = 'error'; message = $_.Exception.Message; at = $_.InvocationInfo.PositionMessage }) 1
}

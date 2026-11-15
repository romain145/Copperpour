---
name: kicad-session
description: Open KiCad 10 on a board (PCB editor) or a whole project (project manager, schematic and PCB editors) and close it again with no one at the screen (Windows and Linux), so an agent can switch between live board edits over IPC and file-only edits (schematics, DRC/ERC severities, project settings) that need KiCad closed. Use whenever a KiCad MCP tool needs "KiCad running with the board open", refuses because KiCad holds a file (`.lck`, "Close Eeschema", "KiCAD currently holds this board open"), or when asked to open, close, reopen or force-close KiCad, or to work on a board unattended.
---

# KiCad session (KiCad 10, Windows and Linux)

On KiCad 10, live board edits need KiCad open, while schematic edits and project-file settings
(`set_drc_severities`, `set_erc_severity`, ...) need it closed: KiCad rewrites those files from
its own copy when it saves, so the MCP server refuses to edit them under it. This skill opens and
closes KiCad itself, so the agent never has to ask the user to. It is a stopgap: KiCad 11's
headless `kicad-cli api-server` will replace it (tracked in romain145/Copperpour#1).

## The script

One script per platform, with the same commands, JSON and exit codes:

```powershell
# Windows (PowerShell 7)
pwsh -File "$env:USERPROFILE\.claude\skills\kicad-session\scripts\session.ps1" <open|close|status> (-Board <x.kicad_pcb> | -Project <x.kicad_pro>) [-Force] [-TimeoutSeconds N]
```

```sh
# Linux (Python 3, standard library only)
python3 ~/.claude/skills/kicad-session/scripts/session.py <open|close|status> (--board <x.kicad_pcb> | --project <x.kicad_pro>) [--force] [--timeout-seconds N]
```

Below, `-Board`/`--board` and the other options are written once in the Windows spelling.

It prints one JSON object. Exit codes: `0` done; `3` refused before doing anything; `4` blocked
(KiCad is left as it is: read `reason`, `unsaved` and `dialogs`); `1` unexpected error.

Two modes; close with the same `-Board` or `-Project` you opened with:

- **`-Board`**: the PCB editor alone (`pcbnew.exe <board>`), ready in about 6 s. Needs no
  setting. Use it for board work.
- **`-Project`**: the project manager with the schematic and PCB editors, in one `kicad.exe`
  behind one IPC socket, ready in about 10 s. It marks both files as open in the project's
  `.kicad_prl`, and KiCad's session restore opens them. It needs KiCad's Preferences > Common >
  "Remember open files for next project launch"; if that is off, `open` refuses
  (`session_restore_off`): ask the user to turn it on, or use `-Board`. Use it when the user
  wants to watch the schematic too. On KiCad 10 the schematic editor is only for viewing: IPC
  cannot edit or save a schematic, and file edits to a schematic are refused while it is open.

Common behavior:

- **`open`** refuses while any other KiCad runs, because the MCP server would see two IPC
  endpoints. Opening what it already opened answers `reused: true`.
- **`close`** closes only what this script opened, checked by PID and command line. It refuses
  if any editor has unsaved changes (window title starting with `*`) or a dialog is on screen.
  Otherwise it closes the PCB editor (board mode) or the project manager (project mode) as a
  user would, and waits for KiCad to exit (under 2 s).
- **`close -Force`** ends KiCad even then: **unsaved changes are lost**. It then removes the
  lock files KiCad left, only those naming this user on this machine, and only when no other
  KiCad runs. Without that cleanup, KiCad would ask about its own stale locks whenever another
  KiCad runs.
- **`status`** lists KiCad processes and their windows, the script's sessions, the target's lock
  files, the IPC pipes and the two KiCad settings it needs.

## Workflow

1. **Live board work**:
   1. `open -Board <board>` (or `-Project <project>`).
   2. Check the MCP server reaches KiCad: `check_kicad_ui` should say `ipc_responsive: true`. In
      board mode, `get_editor_state` also names the open board. With the schematic editor open,
      Konnect's `get_editor_state` fails on KiCad 10 ("malformed schematic document identity"),
      so use `check_kicad_ui` in project mode.
   3. Make the IPC edits.
   4. `save_project` (no arguments: it saves the open board).
   5. `close` with the same `-Board` or `-Project`.
2. **File-only work**: schematic edits, `set_drc_severities` (Konnect v0.13.2 or later),
   `set_erc_severity`, project configuration. Run it with KiCad closed. When a tool says KiCad
   holds a file, check `status`, then `close` if this script opened it.
3. **Back to KiCad**: `open` again. KiCad loads the file edits, and its later saves keep them.

## When it refuses or is blocked

| Result | Do this |
|---|---|
| open: `other_kicad_running` | A KiCad you did not open is running. Never close it. Ask the user to close it, or, if it already shows the right board, use it as it is. |
| open: `session_restore_off` | Ask the user to turn on "Remember open files for next project launch" (Preferences > Common), or use `-Board`. |
| open: `api_server_off` | Ask the user to enable the KiCad API (Preferences > Plugins). |
| open: `dialog_open` | KiCad is asking a question (for example, a file is locked by another user). Nothing has been edited yet, so `close -Force` is safe. Then tell the user what the dialog says; never answer it for them. |
| open: `exited_during_startup` / `timeout` | Retry once; then tell the user, with the JSON. |
| close: `unsaved_changes`, PCB editor | Call `save_project`, then `close` again. |
| close: `unsaved_changes`, schematic editor | The agent cannot save a schematic on KiCad 10, so someone edited it by hand. Tell the user; do not force. |
| close: `dialog_open` / `close_did_not_finish` | Tell the user what the dialog says. |
| close: `not_opened_here` / `process_not_ours` | This KiCad is not yours to close. Never use `Stop-Process`, `taskkill` or window messages on it. |

## When `-Force` is allowed

`-Force` loses every change since the last save. Use it only:

- right after a blocked `open` (nothing edited yet);
- when the user has said the unsaved edits can be thrown away;
- when KiCad no longer responds (`save_project` times out), and the user's instructions allow
  losing the unsaved edits.

It is never a shortcut around `unsaved_changes`: save first. After a forced close, say in your
report that the files are as last saved.

## Requirements and limits

- KiCad 10 with the KiCad API enabled, and the MCP server on KiCad's default socket
  (`%TEMP%\kicad\api.sock` on Windows, `$TMPDIR/kicad/api.sock`, normally `/tmp/kicad`, on Linux).
- **Windows**: KiCad in `C:\Program Files\KiCad\10.0` (otherwise pass `-KiCadBin`), PowerShell 7.
- **Linux**: a native KiCad install with `pcbnew` and `kicad` on `PATH` (otherwise pass
  `--kicad-bin`); Flatpak and Snap installs are untested. An X display is required: `DISPLAY`
  must be set. On a Wayland desktop, the script launches KiCad with `GDK_BACKEND=x11`, so it
  runs under XWayland and its windows can be read and closed. A KiCad you start yourself in
  native Wayland mode is still seen as running (refusal `other_kicad_running`), but its windows
  are not listed. GTK draws a dialog's text itself, so on Linux only a dialog's title is
  reported. The script uses libX11 through ctypes and needs no other tool.
- Unsaved changes are read from the `*` KiCad puts before an editor's title; KiCad 10's IPC has
  no "modified" flag. Dialogs are windows owned by (Windows) or transient for (X11) KiCad's
  frames, which works in any UI language. The project manager is found by its title,
  `<project> — KiCad 10.0`.
- Session records live in `%LOCALAPPDATA%\kicad-session\` (Windows) or
  `~/.local/state/kicad-session/` (Linux), next to KiCad's output log on Linux.
- Linux test harness: `tests/linux-docker/` (KiCad's official `kicad/kicad:10.0` image with Xvfb).
- Tested on 2026-11-15 with KiCad 10.0.8. On Windows 11 the tests used Konnect 0.13.0 and 0.13.2
  over IPC. On Linux (Debian 13 in Docker, Xvfb, once bare and once under the openbox window
  manager) the same board-mode and project-mode cases passed using Copperpour over IPC, with
  KiCad's first-run "KiCad Setup" wizard reported as a dialog when the config was fresh. The
  Windows cases:
  - Board mode:
    - open, then a normal close;
    - an unsaved edit: `close` refuses it, and `close -Force` discards it and removes the locks;
    - a reopen after a forced close, with no lock question;
    - an edit, `save_project`, then a close;
    - a lock held by another user: KiCad's question is reported in 13 s, and that lock is left
      alone;
    - refusals: another KiCad running, and a board this script did not open.
  - Project mode:
    - a refusal while session restore is off;
    - all three windows open in about 10 s, and live board edits work with the schematic editor
      open;
    - `set_drc_severities` and schematic edits are refused while KiCad is open;
    - close, then both edits with KiCad closed, then a reopen; KiCad's next save kept the new
      DRC severity;
    - a forced close of an unsaved board: three locks removed, then a clean reopen.

# Personal Claude Code skills (reference only, not for upstream)

This branch (`personal/claude-skills` on romain145/Copperpour) keeps my own Claude Code skills
next to the Copperpour code I use them with, so they can be pulled onto another machine. It is
not meant to be merged or proposed upstream. Copperpour's own skills are the ones bundled in
`crates/copperpour/assets/skills/`, which `copperpour init` installs.

| Skill | What it does | Platforms |
|---|---|---|
| `kicad-session` | Opens KiCad 10 on a board or a whole project and closes it again with no one at the screen, so an agent can alternate live IPC edits with file-only edits (schematic, DRC/ERC severities). Stopgap until KiCad 11's headless `kicad-cli api-server` (romain145/Copperpour#1). | Windows (`session.ps1`), Linux (`session.py`) |
| `kicad-silk-rules` | Silkscreen rules (one text size, two orientations, labels beside their parts, nothing under a body or cut by the edge, no missing 3D model) and how to make a board meet them; helper scripts for a silk map and a visual check. Tool names are the `pcb_text` ones (Konnect 0.13.1+, Copperpour). | any (Python scripts) |
| `kicad-mech-assembly` | Re-export a board as STEP and replace it in a SolidCraft enclosure assembly at the same position, then check collisions. Needs the SolidCraft MCP server. | any |
| `solidcraft-parametric-practices` | How to keep a SolidCraft model editable: named parameters, feature order, variants, verification. | any |
| `sandboxed-cargo-test` | Build and test a Rust repo (Copperpour PRs, upstream merges) in an offline Docker container from a `git archive` snapshot, so untested code never runs on the host. | Windows; Linux with `pwsh` (untested) |

## Install

Claude Code loads these automatically as project skills when it runs inside this clone. To use
them anywhere, link (or copy) each one into your personal skills folder:

```sh
# Linux / macOS, from the repository root
mkdir -p ~/.claude/skills
for s in kicad-session kicad-silk-rules kicad-mech-assembly solidcraft-parametric-practices sandboxed-cargo-test; do
  ln -sfn "$PWD/.claude/skills/$s" ~/.claude/skills/$s
done
```

```powershell
# Windows, from the repository root
foreach ($s in 'kicad-session','kicad-silk-rules','kicad-mech-assembly','solidcraft-parametric-practices','sandboxed-cargo-test') {
  Copy-Item ".claude\skills\$s" "$env:USERPROFILE\.claude\skills\$s" -Recurse -Force
}
```

On Linux, `kicad-session` needs `python3` and an X display (`DISPLAY`; on a Wayland desktop it
launches KiCad under XWayland). For project mode it needs KiCad's Preferences > Common >
"Remember open files for next project launch". Its Linux behavior was tested in Docker
(`kicad-session/tests/linux-docker/`), not yet on a desktop.

## Not included

- `reverse-engineer-anything`: installed by the REA tool, not mine to republish.
- `konnect`, `kicad-schematic`, `kicad-pcb`, `kicad-review`, `kicad-manufacture`,
  `kicad-library`: installed by `konnect init`; Copperpour bundles its own versions.
- `kicad-visual-check`: an old copy of `kicad-silk-rules`' scripts with no `SKILL.md`.

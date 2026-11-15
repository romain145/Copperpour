---
name: kicad-mech-assembly
description: |
  Export a KiCad board as a STEP model and replace it in a SolidCraft enclosure assembly at the
  same position, then check for collisions. Use after any board change that alters the 3D model
  (parts moved, 3D models relinked, connector or spacer changed) and whenever asked to "re-export
  the 3D model", "update the assembly", "update mechanical", "check the board in the enclosure",
  or to find where a board feature lands relative to another board or the case.
---

# KiCad board into a SolidCraft assembly

## 1. Export the board

1. Save the board in KiCad. Check every model file exists (see `kicad-silk-rules`, rule 9).
2. Export next to the assembly, with the same origin every time:

       kicad-cli pcb export step --subst-models -f -o mechanical/<board>.step <board>.kicad_pcb

   No origin flag: the STEP is in board coordinates (x = board X, y = -board Y, z up from the
   board's back copper side as KiCad defines it). Changing the origin option moves the board in
   the assembly. `--subst-models` swaps a `.wrl` link for the `.step` beside it; without it the
   export fails on a VRML-only link. Konnect's `export_3d` has no such option.
3. Read the output: any "Could not add 3D model" line is a part missing from the assembly.

## 2. Replace it in the assembly

The SolidCraft MCP server only opens files in its own folders. If the assembly is elsewhere and
the server reports "outside the folders this agent may use", copy the `.sc` and the `.step` into
the folder it names, work there, and copy the results back (keep the previous `.sc` as a backup).

1. `open` the assembly. `execute component.list {"compact": true}` (the reply is large: read it
   from the saved file) and note, for the board's component: its id, its parent component and its
   occurrence transform.
2. `timeline.dependents {feature}` on the board's import feature: nothing may depend on it.
3. In one `batch`: `component.activate` the parent, `component.delete` the old board component,
   `file.insert_step` the new file with the same `name` and `matrix`, `component.activate` root.
   The listed transform has the translation in its fourth row; `matrix` takes it as the fourth
   column of a row-major 3x4: `[r00 r01 r02 tx, r10 r11 r12 ty, r20 r21 r22 tz]`.
4. `measure {"body": "<board>_PCB", "summary": true}` before and after: the bounding box must be
   identical. If it moved, the export origin changed.
5. `execute inspect.interference {"budget_s": 200}` and read the pairs that involve the board's
   bodies. Known overlaps (header pins in their socket, a DNP part) are listed in the project
   notes; anything else is a finding.
6. `save`, `export` the assembly as STEP, copy both back.

## 3. Where does a board feature land?

To place a part against something on another board (a spacer on a relay, a connector under a
window), work in board coordinates:

1. Measure the target body (`measure {"body": ...}`) and the board body in the assembly.
2. Take one known point on the board (a mounting hole: board X/Y from KiCad, world position from
   its body's bounding box) to get the mapping between world and board axes and offsets.
3. Convert the target's box to board X/Y. For repeated parts, read each occurrence's translation
   from `component.list` and shift the first box.
4. A position is only usable if it clears, on the board: parts on both sides using their real
   body size (module silkscreen outline, not only the Fab box), tracks and vias on every layer by
   hole radius + hole clearance, and the board edge. Search the whole candidate area before moving
   anything; if every candidate needs parts or tracks moved, report the candidates and what each
   costs instead of picking one.
5. Compare heights too: a spacer that lands on a part must be as long as the gap.

Vias cannot be deleted through Konnect: a new hole position within clearance of a via needs the
via removed by hand first.

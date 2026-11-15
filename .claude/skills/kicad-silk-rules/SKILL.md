---
name: kicad-silk-rules
description: |
  Silkscreen rules for KiCad boards and how to make a board meet them: one text size, two
  orientations, labels beside their parts, nothing cut by the edge, nothing under a component,
  no missing 3D model. Use for any silkscreen work or check, and as part of every board review
  and pre-fab check, even when only "review", "is it ready", "DRC" or "export the 3D model" is
  asked. Triggers: "silkscreen", "silk", "reference designators", "refs overlap", "text cut by
  the edge", "label hidden under a part", "tidy the refs", "3D model missing", "pre-fab review".
---

# KiCad silkscreen rules

The board's DRC is the check. This skill says what the silkscreen must look like, how to set DRC
so it reports what it can, and how to lay the labels out. Text edits go through Konnect
(`list_footprint_fields`, `edit_footprint_field`, `list_board_texts`, `edit_board_text`; Konnect
0.13.1 or later, live board in KiCad). Never edit the `.kicad_pcb` by hand.

## The rules

1. **One size.** Every reference designator on the board has the same glyph height, width and
   stroke. Pick the largest size that fits the tightest spot and use it everywhere: 1.0 / 0.15 mm
   on a roomy board, 0.8 / 0.12, then 0.7 / 0.1. Never below the fab's minimum (the project's
   `min_text_height` / `min_text_thickness`). If one label does not fit, the whole board drops a
   step; a single smaller label is a defect.
2. **Two orientations.** 0° (reads left to right) or 90° (reads bottom to top). Never 180° or
   270°. Prefer 0°; use 90° for a vertical part or where only a vertical slot exists. The back
   side follows the same rule as seen from the back, with mirrored text.
3. **Beside its own part.** The label touches nothing and sits closer to its own part than to
   any other. Keep one side per group (all above, or all to the right) where the space allows.
4. **Stacked parts, stacked labels.** When passives sit in a row or column with no room between
   them, put their labels in a block next to the group, in the same order and direction as the
   parts (three parts stacked top to bottom get three lines top to bottom).
5. **Never under a component.** Not under its own body, not under a neighbour, a module, a
   connector, a battery holder or a spacer. Use the real body size: a module's Fab outline can be
   smaller than the module (check its silkscreen outline and 3D model).
6. **Clear of everything printed or bare.** No overlap with pads, mask openings, holes, other
   text, or footprint outlines. 0.3 mm from the board edge and cut-outs.
7. **Last resort.** A label with no legal place moves to the Fab layer (`layer: F.Fab`), so it is
   in the assembly drawing but not printed. Say which ones. Never shrink or rotate one label to
   squeeze it in.
8. **Board text** (title, pin names, revision) follows rules 2, 5 and 6. Text on the back is
   mirrored. A TrueType title cannot be shrunk far: DRC reports its stroke weight.
9. **3D models.** Every fitted part has a model file that exists. Test points, net ties and
   fiducials have none.

## DRC setup

Raise these in Board Setup > Design Rules > Violation Severity (they are often "ignore"):

| Severity key | Board Setup name | Set to |
|---|---|---|
| `silk_edge_clearance` | Silkscreen clipped by board edge | error |
| `silk_overlap` | Silkscreen overlap | error |
| `silk_over_copper` | Silkscreen clipped by solder mask | error |
| `text_height`, `text_thickness` | Text height / thickness out of range | error |
| `nonmirrored_text_on_back_layer`, `mirrored_text_on_front_layer` | (Non-)mirrored text | error |

Set `min_text_height` and `min_text_thickness` to the size chosen in rule 1. The severities are
in `.kicad_pro`; KiCad rewrites that file while the project is open, so change them in Board Setup
or with KiCad closed. To see what the stricter settings report before touching the project, copy
the `.kicad_pcb` and `.kicad_pro` to a scratch folder, change the severities there and run
`kicad-cli pcb drc --format json --severity-all`.

What DRC does not report (KiCad 10.0): text under a component body, mixed sizes or orientations,
and missing 3D model files. Custom `assertion` rules on text fields were tried and are not usable:
they matched only some footprints and ignored the layer. For these three:

- under a body, sizes, orientations: `list_footprint_fields` gives every label's size, angle and
  box; check them against rules 1, 2 and 5, then look at a top and a bottom render
  (`kicad-cli pcb render --side top|bottom`).
- 3D models: the STEP export prints "Could not add 3D model" for each missing file. A model that
  exists but sits wrong (inside the board, rotated) only shows in the render.

`scripts/board_visual_check.py BOARD` is a read-only helper that lists edge, under-body, overlap
and model-file findings from the saved file. Its text boxes are estimates: it is a place to start
looking, not the verdict.

## Laying the labels out

1. Save the board. Run DRC with the strict severities. List all fields and board texts.
2. Choose the size (rule 1) from the tightest group on the board, before moving anything.
3. Draw the map: `python scripts/silk_map.py BOARD OUT.png --side F --window x1,y1,x2,y2`
   (bodies grey, pads gold, footprint silkscreen white, text boxes, 1 mm grid in board
   coordinates). Read part extents and free gaps from it.
4. Place group by group, writing down the centre and angle of every label first. A 3-character
   label at height h is about 2.8 h long and h high. A gap narrower than h + 0.2 mm cannot take a
   label: use a block (rule 4) or a 90° label instead.
5. Apply every label in one batch with size, stroke, position and angle together, including the
   labels that do not move, so the size is uniform.
6. Save, run DRC again, render both sides and read every label in the render. Repeat for what
   is left.

`scripts/plan_silk_moves.py` proposes places automatically. On a dense board its answers are
scattered and need the hand pass above; use it for sparse boards or as a first guess.

## Known limits

- Editing a footprint field rewrites the footprint through KiCad's API: expect
  `lib_footprint_mismatch` warnings on edited footprints with a rule area or unit mapping.
- `edit_board_text` cannot mirror text. Moving a board text to the back leaves it unmirrored:
  tick Mirrored in its properties in KiCad.
- An update of footprints from the library resets the reference position, size and 3D model
  path of each footprint. Run the checks again after one.
- With the PCB and schematic editors both open, Konnect 0.13.0 refuses edits ("BeginCommit
  without a document specified"); 0.13.1 fixes it.

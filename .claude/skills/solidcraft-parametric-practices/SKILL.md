---
name: solidcraft-parametric-practices
description: Best practices for parametric CAD in SolidCraft. Use whenever you build, restructure, review or clean up a SolidCraft design and it should stay editable, even if the user only says "make it parametric", "clean up the model", "does this follow best practices", or asks for a part with several sizes or variants. Use alongside SolidCraft's bundled solidcraft-modeling and solidcraft-editing references, which cover command shapes.
---

# SolidCraft parametric practices

A parametric model is good when someone can change a number months later and get a correct part, with no surprises. Everything below serves that. For command syntax, load `skill_reference {"skill":"solidcraft-modeling"}`, and `solidcraft-editing` or `solidcraft-checking` when editing or validating.

## Before modelling

1. Run `inspect_design {"measure":false,"sketches":false}`. The user may have changed the design since you last looked, so never rely on an earlier read.
2. Separate what is known from what is guessed. A wrong number that looks measured is the most expensive defect in a model.
3. Work out the expected volume and bounding box for each stage, so a check can actually fail.

## Parameters

- Name every size that a person might change, in plain words (`flange_t`, not `d3`). Literals in expressions hide intent and get missed later.
- The only literals worth keeping are cutter overshoots, such as starting a cut 1 mm past a face, because they don't describe the part.
- Comment each parameter with its source and allowed range: `Measured (datasheet)` or `ASSUMED: measure on a real part`, then `Range: ...`. SolidCraft enforces no range during a parameter change, so the comment is the only guard a user sees.
- Derive a size from another only when the design really links them. If two sizes are independent on the real part, keep two parameters.
- Mark the few driving parameters as favourites.

## Feature order and structure

- Make the first feature the real base shape. Avoid placeholder features left behind to keep a timeline alive.
- Define a repeated thing once, then mirror or pattern it. Two copies of one formula will eventually drift apart.
- Give each separately made or differently made part its own body: different material means different body. Avoid two bodies occupying the same space; cut a pocket for an inserted part.
- Put joins before the cuts that must stay open, because a later join fills them.
- Overlap joins into the body rather than joining on a shared face. Avoid coincident cylindrical walls where a slightly smaller or larger feature would do.
- Name features after what they are. Prefer primitives with expression fields for simple shapes, and sketches when the profile needs them. Every sketch ends at 0 degrees of freedom.

## Variants

When a part comes in several sizes, use a configuration table (`config.configure`, `config.row`, `config.activate`) instead of asking people to retype values. Preview each row with `config.preview`.

## Materials and mass

Assign a material when mass matters, and say when it can't be trusted. A composite such as a wire winding is not solid metal: use an effective density from a known mass or wire volume, and name the material so the assumption is visible.

## Editing an existing design

- Change inputs rather than rebuilding, unless the structure itself is wrong. `timeline.edit` changes a feature's fields, but `timeline.redefine` can't change its type, so a cylinder can't become a box.
- To insert mid-history: `timeline.roll_to`, add the feature, then roll to the end. Check every later feature afterwards.
- When restructuring, take `edit.checkpoint` first, and put risky steps in their own `batch`, because a failed batch rolls back everything in it.

## Verifying

- Give each batch an `expect` block with your computed volume, body count and bounding box. `ok: true` alone only means nothing crashed.
- Vary every driving parameter, not just one, with `check_parameters`, towards both ends of its stated range. Check for errors, body count and closed bodies, and compare at least one state with a hand calculation.
- Confirm positions with `body_topology {"faces":"cylinders"}`, and look at a screenshot from an angle that shows the feature you changed.

## Saving

- The app can quit without warning and take unsaved work with it, so tell the user early when a design is unsaved.
- The MCP `save` is limited to the agent's allowed folders. If the path is refused, ask the user to save from the app or add `--allow-path`. Don't report a model as saved until a save has succeeded.

## Reporting back

Say which sizes are measured and which are assumed, what was tested, and what SolidCraft can't enforce. If the numbers are still placeholders, say so before anything else.

# Linux test harness for `session.py`

Runs `session.py` against real KiCad 10 in KiCad's official Docker image, under a virtual X
server, and edits the board over IPC through a Copperpour MCP server to make it unsaved and to
save it. `run_tests.sh` covers, in board mode and project mode:

- open;
- an unsaved edit, which `close` refuses and `close --force` discards (locks removed);
- an edit, a save and a close;
- a lock held by another user, reported as a dialog and left in place;
- the refusals for another KiCad running and for a target this script did not open;
- a reopen with both editors restored.

It needs a Linux build of Copperpour (any recent one with `check_kicad_ui`, `add_board_text`
and `save_project`), mounted into the container.

```sh
# from the skill's root folder (.claude/skills/kicad-session)
docker build -f tests/linux-docker/Dockerfile -t kicad-session-test .

# Copperpour built on the host (Linux): mount the folder that holds the binary
docker run --rm --network none --shm-size 512m \
  -v /path/to/Copperpour/target/debug:/opt/cp:ro kicad-session-test /opt/test/run_tests.sh

# again under a window manager, as on a desktop
docker run --rm --network none --shm-size 512m -e WM=openbox \
  -v /path/to/Copperpour/target/debug:/opt/cp:ro kicad-session-test /opt/test/run_tests.sh

# or a build left in the sandboxed-cargo-test skill's cache volume
docker run --rm --network none --shm-size 512m -e COPPERPOUR=/opt/cp/debug/copperpour \
  -v sct-cache-copperpour_target:/opt/cp:ro kicad-session-test /opt/test/run_tests.sh
```

The test KiCad config is written by `run_tests.sh`. It turns on the API server and session
restore, and sets the two flags and the design-block library table that keep KiCad 10's
first-run "KiCad Setup" wizard from opening. Without them, every `open` reports that wizard as
a blocking dialog, which is also a useful check.

Fixtures: `fixtures/proj/demo.kicad_pcb` is Copperpour's
`crates/copperpour-core/tests/fixtures/board_outline_kicad10.kicad_pcb`; `fixtures/pp/amp.*` is
`crates/copperpour-sexp/tests/fixtures/placement/placement_fixture.*`, renamed.

---
name: sandboxed-cargo-test
description: Build and test a Rust repo inside an offline Docker container, from a read-only snapshot of a commit, so untrusted code never runs on the host. Use whenever cargo test, cargo build or cargo clippy would execute code you or the user did not write and have not fully vetted - cherry-picked or merged upstream commits, a contributor's PR or fork, a freshly cloned third-party repo, new or bumped dependencies - and whenever a cargo command is refused by the permission classifier as "Code from External". Also use when asked to "run the tests in docker", "sandbox the tests", or "test this safely".
---

# Sandboxed cargo test

Compiling and testing Rust executes arbitrary code: build scripts, proc macros
and the tests themselves. When some of that code comes from outside, run it in
a container instead of on the host. This skill bundles a Dockerfile, a compose
file and a driver script that do so.

## What the sandbox guarantees

- **Source**: a `git archive` of one commit, copied into a Docker volume with
  `docker cp` and unpacked inside the container. No host folder is mounted at
  all: the working tree, other worktrees, `~/.ssh`, credentials and environment
  stay out of reach. (It also avoids Docker Desktop's Windows file sharing,
  which can hang every container that bind-mounts a host folder.)
- **Network**: dependencies are downloaded first by `cargo fetch --locked`,
  which runs no build scripts. The build and tests then run with
  `network_mode: none`.
- **Privileges**: all capabilities dropped, `no-new-privileges`, a pid limit.
- **Toolchain**: the `rust:<channel>-bookworm` image, with the channel read
  from `rust-toolchain.toml`.

## Run it

Use the PowerShell tool. The run takes minutes on a cold cache, so run it in
the background and wait for the notification. On Linux, run the same script
with PowerShell 7 (`pwsh -File ~/.claude/skills/sandboxed-cargo-test/scripts/run.ps1 ...`)
and a local Docker engine; this was written and tested on Windows with Docker
Desktop, and is untested on Linux.

```powershell
& "$env:USERPROFILE\.claude\skills\sandboxed-cargo-test\scripts\run.ps1" -Repo <repo path> [-Ref <commit-ish>] [-Cargo '<cargo command line>']
```

- Without `-Cargo` it runs `cargo test --locked --workspace --no-fail-fast`.
- `-Cargo` takes the whole cargo command line as one quoted string, for example
  `-Cargo 'test --locked -p crate-a -p crate-b --no-fail-fast'` or
  `-Cargo 'clippy --locked --workspace -- -D warnings'`. It must be one string:
  passed loose, cargo flags such as `-p` are taken by PowerShell as parameters
  of the script and the call fails.
- `-Ref` defaults to `HEAD`. Only committed content is tested; the script warns
  when the tree has uncommitted changes. Commit (or stash and test a temporary
  commit) if those changes matter.
- The script prints the failing and summary lines, then `CARGO_EXIT=<code>` and
  `LOG=<path>`; read the log for detail. Its exit code is cargo's.

## Caches, parallel runs and disk

Build output and the cargo home live in two Docker volumes per repository,
`sct-cache-<repo>_target` and `sct-cache-<repo>_cargo-home`, shared by every
worktree of that repo. A target directory per worktree costs several GB each
and has filled the Docker disk when several agents built at once.

- Runs may be started in parallel, from one worktree or several, but they
  execute one at a time: each takes an exclusive lock before unpacking its
  source and holds it until cargo exits. `sandbox: waiting for the build lock`
  in the log is that queue, not a hang. Each run copies its source into its own
  snapshot volume (commit and PID in the name, removed at the end) and writes
  its own `cargo-<pid>.log`. Before 2026-11-15 the snapshot volume was named
  after the worktree only, so a queued run could build the source of a run
  started after it; distrust results from parallel runs made before then.
- Dependencies are compiled once. The repo's own crates recompile on every
  run. That is deliberate: cargo judges freshness by file time, so the source
  is unpacked with the current time, under the lock, to be newer than anything
  an earlier run built. Do not "optimise" this away (no unpacking before the
  lock, no keeping commit timestamps): an earlier version did, and one
  worktree was compiled against another worktree's crates, which can produce a
  false pass as easily as a false failure.
- `No space left on device` means the Docker disk is full. Check
  `docker system df`. The `sct-cache-*` volumes are safe to delete (they are
  caches); images and build cache that this skill did not create belong to the
  user, so ask before pruning those.

## Checking that Windows-only code compiles

The container cannot run Windows tests, but it can type-check the
`cfg(windows)` code by cross-compiling, which catches build breaks before CI:

```powershell
& "$env:USERPROFILE\.claude\skills\sandboxed-cargo-test\scripts\run.ps1" -Repo <repo path> `
  -AptPackages 'protobuf-compiler libprotobuf-dev cmake clang libclang-dev mingw-w64 nasm' `
  -RustTargets 'x86_64-pc-windows-gnu' `
  -Cargo 'check --locked --tests --target x86_64-pc-windows-gnu --workspace'
```

Report this as "builds for Windows", never as "tested on Windows". To run the
tests on Windows, use the project's CI (check its triggers: many workflows run
only on pull requests, so a pushed branch alone starts nothing) or a native run
by the user.

## When a run fails before any test runs

These are setup gaps, not test failures. Fix the image and rerun.

| Symptom in the log | Cause | Fix |
|---|---|---|
| `failed to execute command: No such file or directory` from a `-sys` crate's build script | a native tool (cmake, nasm, pkg-config library) is missing | add the Debian package with `-AptPackages "<defaults> <extra>"` |
| `could not download file from 'https://static.rust-lang.org/...'` | `rust-toolchain.toml` asks for a component or target the image lacks | add a `rustup component add` / `rustup target add` line to `docker/Dockerfile` |
| `failed to list workers ... EOF` during the image build | Docker Desktop builder hiccup | the script already retries once; run again |
| `no matching package` / attempts to reach crates.io during the test step | `Cargo.lock` missing or out of date, so `fetch --locked` did not cover the build | fix the lockfile in the repo first |

## Report honestly what the run does and does not show

A green run here is a Linux result. State these limits when reporting:

- Code behind `cfg(windows)` or `cfg(target_os = "macos")` is not compiled or
  tested. On a Windows or macOS host, a native run or CI is still needed.
- Tests marked `#[ignore]` stay ignored, as do tests that need an external
  program (a GUI application, a licence server, real hardware).
- Debian's packaged tools can differ in version from what CI pins (for example
  `protoc`). Check the CI workflow when a version-sensitive failure appears.
- `git archive` omits submodules and anything `.gitattributes` marks
  `export-ignore`.

Give the pass, fail and ignored counts per crate, taken from the
`test result:` lines.

## The sandbox is not a review

Running in a container limits what hostile code can reach; it says nothing
about whether the code is safe to merge or to run on the host later. If the
user asks whether external commits are harmful, read the diff as well: build
scripts, `Cargo.toml`/`Cargo.lock` changes, process spawning, network, file
deletion, and what the tests themselves execute.

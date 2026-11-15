<#
.SYNOPSIS
Run cargo against a committed snapshot of a Rust repo inside an offline Docker
container.

.EXAMPLE
run.ps1 -Repo Z:\git\github\Konnect
run.ps1 -Repo . -Ref fix/batch-1 -Cargo 'test --locked -p konnect-sexp -p konnect-core --no-fail-fast'
run.ps1 -Repo . -Cargo 'clippy --locked --workspace -- -D warnings'
#>
param(
    [string]$Repo = (Get-Location).Path,
    [string]$Ref = 'HEAD',
    [string]$AptPackages = 'protobuf-compiler libprotobuf-dev cmake clang libclang-dev',
    # Extra rustup targets to install in the image, space separated.
    [string]$RustTargets = '',
    # The cargo command line as ONE string, e.g. 'test --locked -p my-crate'.
    # A string, not loose arguments: PowerShell would otherwise try to bind
    # cargo flags such as -p to this script's own parameters.
    [string]$Cargo = ''
)
$CargoArgs = @($Cargo -split '\s+' | Where-Object { $_ })

$ErrorActionPreference = 'Stop'
$compose = Join-Path $PSScriptRoot '..\docker\compose.yaml' | Resolve-Path
$Repo = (Resolve-Path $Repo).Path

$sha = git -C $Repo rev-parse --verify "$Ref^{commit}"
if ($LASTEXITCODE -ne 0) { throw "'$Ref' is not a commit in $Repo" }

# Only committed content is tested. Say so when the tree has more than that.
$dirty = git -C $Repo status --porcelain --untracked-files=no
if ($dirty -and $Ref -eq 'HEAD') {
    Write-Warning "Uncommitted changes in $Repo are NOT in the snapshot; only commit $($sha.Substring(0,7)) is tested."
}

# Toolchain channel from rust-toolchain.toml, else the latest stable 1.x image.
$channel = '1'
foreach ($file in 'rust-toolchain.toml', 'rust-toolchain') {
    $path = Join-Path $Repo $file
    if (Test-Path $path) {
        $text = Get-Content $path -Raw
        if ($text -match '(?m)^\s*channel\s*=\s*"([^"]+)"') { $channel = $Matches[1] }
        elseif ($text.Trim() -match '^[\w.\-]+$') { $channel = $text.Trim() }
        break
    }
}

$name = (Split-Path $Repo -Leaf).ToLower() -replace '[^a-z0-9]', '-'
$project = "sct-$name"
# GetTempPath, not $env:TEMP: Linux sets no TEMP variable.
$snapshot = Join-Path ([IO.Path]::GetTempPath()) "sandboxed-cargo-test/$name-$($sha.Substring(0,12))"
New-Item -ItemType Directory -Force $snapshot | Out-Null
git -C $Repo archive --format=tar -o (Join-Path $snapshot 'src.tar') $sha
if ($LASTEXITCODE -ne 0) { throw 'git archive failed' }

$env:RUST_VERSION = $channel
$env:APT_PACKAGES = $AptPackages
$env:RUST_TARGETS = $RustTargets
# Build caches are keyed by the repository, not the worktree, so every
# worktree of one repo shares a single registry and target volume.
$common = git -C $Repo rev-parse --path-format=absolute --git-common-dir
$repoName = (Split-Path (Split-Path $common -Parent) -Leaf).ToLower() -replace '[^a-z0-9]', '-'
$env:CACHE_PREFIX = "sct-cache-$repoName"
docker volume create "$($env:CACHE_PREFIX)_cargo-home" | Out-Null
docker volume create "$($env:CACHE_PREFIX)_target" | Out-Null
# One snapshot volume per run, never shared. A volume named after the worktree
# alone was shared by every run from that worktree: a queued run's src.tar was
# replaced by the next run's `docker cp` before it took the build lock, so it
# built another commit's source (seen 2026-11-15: a gate run tested a
# negative-control commit and reported its failure). The volume is removed
# when the run ends.
$volume = "${project}_snapshot_$($sha.Substring(0,12))_$PID"
$env:SNAPSHOT_VOLUME = $volume
$env:BUILD_COMMIT = $sha
$dc = @('compose', '-f', "$compose", '-p', $project)

Write-Host "== $name @ $($sha.Substring(0,7)), rust $channel, project $project"

# Docker Desktop's builder sometimes drops the first connection (EOF); retry once.
docker @dc build -q test
if ($LASTEXITCODE -ne 0) {
    docker @dc build -q test
    if ($LASTEXITCODE -ne 0) { throw 'image build failed' }
}

# Copy the snapshot into a named volume through a container that never starts.
# No host folder is mounted: Docker Desktop's Windows file sharing can hang.
docker volume create $volume | Out-Null
$loader = "$project-loader-$PID"
docker create --name $loader -v "${volume}:/in" "sandboxed-cargo-test:$channel" true | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'could not create the snapshot loader container' }
docker cp (Join-Path $snapshot 'src.tar') "${loader}:/in/src.tar"
$copied = $LASTEXITCODE
docker rm $loader | Out-Null
if ($copied -ne 0) { throw 'could not copy the snapshot into the volume' }

try {
    Write-Host '== fetching locked dependencies (network on, no project code runs)'
    docker @dc run --rm fetch 2>&1 | Select-Object -Last 3
    if ($LASTEXITCODE -ne 0) { throw 'cargo fetch failed' }

    # One log per run: two runs of the same commit used to share cargo.log.
    $log = Join-Path $snapshot "cargo-$PID.log"
    Write-Host "== running cargo offline; full log: $log"
    if ($CargoArgs.Count -gt 0) { docker @dc run --rm test @CargoArgs 2>&1 | Tee-Object -FilePath $log | Out-Null }
    else { docker @dc run --rm test 2>&1 | Tee-Object -FilePath $log | Out-Null }
    $code = $LASTEXITCODE
}
finally {
    docker volume rm $volume 2>&1 | Out-Null
}

Select-String -Path $log -Pattern '^error|^warning: unused|could not compile|panicked at|^test .* FAILED|^failures:|test result:' |
    ForEach-Object { $_.Line } | Select-Object -Last 80
Write-Host "CARGO_EXIT=$code"
Write-Host "LOG=$log"
exit $code

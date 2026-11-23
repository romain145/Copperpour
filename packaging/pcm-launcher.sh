#!/bin/sh
# Copperpour server launcher for the multi-platform PCM package (#398).
#
# merge-pcm.py installs this as plugins/bin/copperpour, the documented path MCP
# clients point at on Linux and macOS (layout: docs/KICAD_INTEGRATION.md). It
# execs plugins/bin-<platform>/copperpour, so stdin, stdout, stderr, the
# arguments, the PID and the exit status are the server's own. It never writes
# to stdout, which carries MCP.

self=$0
hops=0
# Follow symlinks to this file, so a link on PATH still finds the package.
while [ -L "$self" ]; do
    hops=$((hops + 1))
    if [ "$hops" -gt 40 ]; then
        echo "copperpour: too many levels of symbolic links: $0" >&2
        exit 126
    fi
    link=$(readlink -- "$self") || exit 126
    case $link in
        /*) self=$link ;;
        *) self=$(dirname -- "$self")/$link ;;
    esac
done
plugins=$(CDPATH='' cd -P -- "$(dirname -- "$self")/.." && pwd) || exit 126

case $(uname -s) in
    Linux)
        case $(uname -m) in
            x86_64 | amd64) platform=linux-x86_64 ;;
            # Packaged on its own (build-pcm.sh --platform linux-aarch64): with
            # it, this package would pass validate-pcm.py's download cap.
            aarch64 | arm64)
                echo "copperpour: no server for Linux $(uname -m) in this package;" \
                    "install the release's copperpour-pcm-v<version>-linux-aarch64.zip" \
                    "from https://github.com/Copperpour/Copperpour/releases" >&2
                exit 126
                ;;
            *)
                echo "copperpour: no server for Linux $(uname -m) in this package" >&2
                exit 126
                ;;
        esac
        ;;
    Darwin) platform=macos ;;
    *)
        echo "copperpour: no server for $(uname -s) in this package" >&2
        exit 126
        ;;
esac

exec "$plugins/bin-$platform/copperpour" "$@"

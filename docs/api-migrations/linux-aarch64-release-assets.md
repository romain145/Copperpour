### Releases add Linux aarch64 builds (minor release)

Releases now carry a Linux aarch64 server, built by `linux-aarch64.yml` on
GitHub's arm64 runners: the archive
`copperpour-v<version>-aarch64-unknown-linux-gnu.tar.gz`, the KiCad PCM package
`copperpour-pcm-v<version>-linux-aarch64.zip` and the Claude Desktop extension
`copperpour-v<version>-linux-aarch64.mcpb`. Every existing asset keeps its name
and contents.

The all-platforms PCM package `copperpour-pcm-v<version>.zip` still carries
Windows, macOS and Linux x86_64 only, because a fourth server would push it
past the download cap. On an aarch64 machine its `bin/copperpour` launcher
still exits with status 126, and its message now names the Linux aarch64 zip.
On 64-bit Arm Linux, install that zip with **Install from File**. It puts the
server at the documented `bin/copperpour` path. Neither PCM nor MCPB has a CPU
field, so both new packages declare `linux`: choose them by file name.

`packaging/build-pcm.sh` and `packaging/build-mcpb.py` accept
`--platform linux-aarch64`. `packaging/merge-pcm.py` refuses the aarch64 zip as
a second Linux package, so name the three zips it merges.

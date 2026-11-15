ARG RUST_VERSION=1
FROM rust:${RUST_VERSION}-bookworm

# Native build dependencies. The defaults cover protobuf codegen (protoc and
# the well-known .proto includes) and crates that build C code with cmake or
# generate bindings with clang.
ARG APT_PACKAGES="protobuf-compiler libprotobuf-dev cmake clang libclang-dev"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ${APT_PACKAGES} \
    && rm -rf /var/lib/apt/lists/*

# A rust-toolchain.toml that lists these makes rustup download them on first
# use, which fails in the offline test container. Install them here instead.
RUN rustup component add clippy rustfmt

# Extra compile targets, e.g. x86_64-pc-windows-gnu for a Windows cross-check
# (which also needs the mingw-w64 apt package).
ARG RUST_TARGETS=""
RUN if [ -n "${RUST_TARGETS}" ]; then rustup target add ${RUST_TARGETS}; fi

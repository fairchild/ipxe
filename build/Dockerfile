# Standalone iPXE builder: compiles the four custom binaries and exports them
# so `build.sh` can drop them in build/dist/ and record their sha256s.
# The bootstrap container reuses compile-ipxe.sh in its own builder stage; this
# file exists for local builds and for producing R2 upload artifacts.
#
#   docker build -o dist build          # or run build/build.sh
# Pinned to amd64 so one builder cross-compiles every iPXE target the same way
# regardless of the host/CI platform (arm64 hosts run it under emulation).
FROM --platform=linux/amd64 debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential liblzma-dev git perl ca-certificates \
      gcc-i686-linux-gnu binutils-i686-linux-gnu \
      gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu \
    && rm -rf /var/lib/apt/lists/*

ARG IPXE_SERVER_URL=https://ipxe.cloudcompute.com
ENV IPXE_SERVER_URL=${IPXE_SERVER_URL}

WORKDIR /build
COPY . /build
RUN OUTPUT_DIR=/out sh /build/compile-ipxe.sh

# Export stage: `docker build -o dist` writes just the binaries to the host.
FROM scratch AS export
COPY --from=builder /out/ /

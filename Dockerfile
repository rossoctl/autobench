# Pure-Python image: no bash/curl/jq/kubectl. Arbitrary-UID-safe for OpenShift.
FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY README.md ./
# Vendor deps + the package into a self-contained venv. Use pip (not uv) so the
# build survives QEMU emulation for cross-arch (multi-arch) buildx: uv's binary
# segfaults under emulated amd64, whereas pip installs prebuilt wheels cleanly.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir .

FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" \
    SERVICE_INSTANCES_DIR=/etc/service/instances \
    SERVICE_PORT=8080
COPY --from=build /opt/venv /opt/venv

# OpenShift's restricted-v2 SCC assigns a random non-root UID in the namespace's
# range; that UID is not in /etc/passwd and always belongs to GID 0. Make the
# runtime dirs group-owned by root (GID 0) and group-writable so the image works
# under BOTH a pinned UID 10001 (kind/generic k8s) and an arbitrary injected UID
# (OpenShift). Rossoctl's own UI/backend pods pin no UID and rely on this SCC
# injection; we additionally pin 10001 as the default for non-OpenShift targets.
RUN mkdir -p /etc/service/instances \
    && chgrp -R 0 /opt/venv /etc/service \
    && chmod -R g=u /opt/venv /etc/service

# `image.source` is what links the GHCR package to this repository — without it the published
# package shows no repo, no README and no license on its GHCR page.
#
# THESE LABELS ARE NOT ENOUGH ON THEIR OWN for a multi-arch push. A LABEL lands in each child
# image's *config*, but GHCR reads `org.opencontainers.image.source` from the **index annotations**,
# and it does not traverse the index to find the labels. Pushing a manifest list whose index has no
# annotations leaves the package unlinked (observed: `repository: null` even with these labels
# present and readable on both arches). So the build MUST also pass index-level annotations:
#
#   docker buildx build --builder multi --platform linux/amd64,linux/arm64 \
#     --annotation "index:org.opencontainers.image.source=https://github.com/rossoctl/autobench" \
#     --annotation "index:org.opencontainers.image.title=AutoBench" \
#     --annotation "index:org.opencontainers.image.description=Automated benchmarking of agentic AI workloads on Rossoctl" \
#     --annotation "index:org.opencontainers.image.licenses=Apache-2.0" \
#     --output type=oci,dest=/tmp/autobench-oci.tar -t ghcr.io/rossoctl/autobench:<tag> .
#   skopeo copy --all --retry-times 8 oci-archive:/tmp/autobench-oci.tar \
#     docker://ghcr.io/rossoctl/autobench:<tag>
#
# (`--output type=oci` + `skopeo copy --all` rather than `--push` works around a buildkit 60s
# push timeout; `skopeo --all` is what preserves the multi-arch index.)
LABEL org.opencontainers.image.source="https://github.com/rossoctl/autobench" \
      org.opencontainers.image.title="AutoBench" \
      org.opencontainers.image.description="Automated benchmarking of agentic AI workloads on Rossoctl" \
      org.opencontainers.image.licenses="Apache-2.0"

USER 10001
EXPOSE 8080
ENTRYPOINT ["autobench-service"]

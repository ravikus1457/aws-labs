# syntax=docker/dockerfile:1
#
# Lab 06 app image — multi-stage, non-root, base pinned by digest.
#
#   stage "test"    : runs the unit tests + byte-compiles. A red test = no image.
#   stage "runtime" : only app.py + a non-root user. Nothing to compile, no shell
#                     tricks, no package manager calls, so no apt/pip CVE churn.
#
# The base is pinned to the multi-arch manifest digest of python:3.12-slim-bookworm
# (resolved 2026-10-01). To bump: docker buildx imagetools inspect python:3.12-slim-bookworm
# and update BOTH FROM lines (kept literal so hadolint can see the tag + digest).

FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS test
WORKDIR /src
COPY app.py test_app.py ./
RUN python3 -m unittest -v test_app && python3 -m compileall -q app.py

FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS runtime
ARG APP_VERSION=dev
ARG GIT_SHA=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    APP_VERSION=${APP_VERSION} \
    GIT_SHA=${GIT_SHA}

# Fixed, high UID/GID: the ECS task definition sets user "10001:10001" too, so
# the container runs non-root even if someone drops the USER line below.
RUN groupadd --system --gid 10001 app \
 && useradd  --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=test --chown=10001:10001 /src/app.py /app/app.py

USER 10001:10001
EXPOSE 8080

# Docker-level healthcheck (used by `docker run` locally; ECS uses the task
# definition's healthCheck + the ALB target group instead).
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python3", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"]

LABEL org.opencontainers.image.title="lab06-app" \
      org.opencontainers.image.source="https://github.com/ravikus1457/aws-labs" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.version="${APP_VERSION}"

CMD ["python3", "/app/app.py"]

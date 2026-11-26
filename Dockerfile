# syntax=docker/dockerfile:1

# ---- build stage: full Python toolchain; installs dependencies into /deps ----
# Python minor version must match the runtime image (distroless debian13 = 3.13),
# because some wheels (tiktoken, pydantic-core, regex) contain compiled code.
FROM python:3.13-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-compile --target /deps -r /tmp/requirements.txt \
 && TIKTOKEN_CACHE_DIR=/tiktoken PYTHONPATH=/deps \
    python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
WORKDIR /app
COPY kbsync ./kbsync
COPY main.py ask.py ./
RUN mkdir -p out articles

# ---- runtime stage: distroless Python, no shell, no pip, runs as non-root ----
FROM gcr.io/distroless/python3-debian13:nonroot
ENV PYTHONPATH=/deps \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken
COPY --from=build /deps /deps
COPY --from=build /tiktoken /opt/tiktoken
COPY --from=build --chown=65532:65532 /app /app
WORKDIR /app

# `docker run IMAGE` and `docker run IMAGE main.py [--dry-run|--mark-stale N]` both work;
# `docker run IMAGE ask.py "question"` runs the sanity check.
ENTRYPOINT ["/usr/bin/python3"]
CMD ["main.py"]

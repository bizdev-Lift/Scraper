# Use the official lightweight Python image.
# https://hub.docker.com/_/python
FROM python:3.13.0-slim-bookworm AS base

ARG UV_VERSION=0.6.12
ARG APP_NAME=app


ENV APP_PATH=/opt/$APP_NAME
WORKDIR $APP_PATH

ENV                     \
  PYTHONFAULTHANDLER=1  \
  PYTHONUNBUFFERED=1    \
  PYTHONHASHSEED=random

ENV \
  PIP_NO_CACHE_DIR=off \
  PIP_DISABLE_PIP_VERSION_CHECK=on \
  PIP_DEFAULT_TIMEOUT=100

ENV UV_HOME=/opt/uv \
    UV_CACHE_DIR=/tmp/poetry_cache \
    # Compiling Python source files to bytecode tends to
    # improve startup time (at the cost of increased installation time).
    UV_COMPILE_BYTECODE=1


ENV PATH="$UV_HOME/bin:$PATH"

RUN python -m pip install --upgrade pip \
 && pip install uv==$UV_VERSION \
 && uv tool install keyring --with keyrings.google-artifactregistry-auth \
 && apt-get update \
 && apt-get install -y --no-install-recommends procps curl \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

 FROM base AS builder

 WORKDIR $APP_PATH
 # Copy local code to the container image.
 COPY pyproject.toml ./
 COPY uv.lock ./
 RUN --mount=type=cache,target=$UV_CACHE_DIR \
     --mount=type=secret,id=gcp_secret,target=/var/run/secrets/google/gcp_credentials.json \
   GOOGLE_APPLICATION_CREDENTIALS=/var/run/secrets/google/gcp_credentials.json \
   uv sync  \
   --no-install-project # Similar to --no-root in poetry \
   --frozen # Sync without updating the lock file

ENV PATH="$APP_PATH/.venv/bin:$PATH"
ENV VIRTUAL_ENV="$APP_PATH/.venv"
RUN uv pip install awslambdaric

FROM base AS llm-extractor

ENV PATH="$APP_PATH/.venv/bin:$PATH"
ENV VIRTUAL_ENV="$APP_PATH/.venv"
WORKDIR $APP_PATH

COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
# COPY --from=builder /root/.cache/ms-playwright /root/.cache/ms-playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/
RUN playwright install chromium --with-deps

COPY io_operations ./io_operations
COPY llm ./llm
COPY scraper ./scraper
COPY hubspot ./hubspot
COPY apollo ./apollo
COPY seamless ./seamless
COPY hubspot ./hubspot
COPY _types.py _types.py
COPY executor.py executor.py
COPY logger.py logger.py
COPY config.py config.py
COPY credentials.json credentials.json
COPY lambdas ./lambdas
COPY main.py main.py
COPY response.json response.json

ENTRYPOINT ["python", "-m", "awslambdaric" ]

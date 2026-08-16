# syntax=docker/dockerfile:1.7
#
# Two build targets from one file:
#   INSTALL_ML=false  -> api / worker-io / beat   (~250MB, seconds to rebuild)
#   INSTALL_ML=true   -> worker-ml                (~1.2GB, holds torch)
#
# torch is pinned to the CPU wheel index on purpose. The RTX 5060 is Blackwell
# (sm_120), unsupported by stable CUDA 12.4 wheels, and its 8GB of VRAM is
# reserved for Ollama (§12.4). §27.1 budgets CLIP as CPU-bound at demo scale.

FROM python:3.14-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# libgl/libglib come from opencv (transitive via mediapipe). libegl/libgles are
# required by MediaPipe 1.x's Tasks runtime, which links its GPU delegate at
# load time even when inference runs on CPU — without them the face detector
# fails with "libEGL.so.1: cannot open shared object file" only at first use,
# well after the image builds successfully.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        libgl1 \
        libglib2.0-0 \
        libegl1 \
        libgles2 \
        procps \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer, cached independently of source so editing a module does not
# re-resolve the tree.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-cache-dir -r pyproject.toml

# Extras are resolved from pyproject.toml rather than restated here. A
# duplicated dependency list is a second source of truth that drifts silently —
# the first symptom is a test importing a package the image never installed.
ARG INSTALL_ML=false
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$INSTALL_ML" = "true" ]; then \
        uv pip install --system --no-cache-dir \
            --index-url https://download.pytorch.org/whl/cpu \
            "torch>=2.5.0" && \
        uv pip install --system --no-cache-dir \
            --extra-index-url https://download.pytorch.org/whl/cpu \
            --index-strategy unsafe-best-match \
            -r pyproject.toml --extra ml ; \
    fi

ARG INSTALL_DEV=false
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$INSTALL_DEV" = "true" ]; then \
        uv pip install --system --no-cache-dir -r pyproject.toml --extra dev ; \
    fi

COPY . .

# Non-root. The volume mount points are chowned so uploads and model weights
# remain writable without granting broad filesystem access.
RUN useradd --create-home --uid 10001 nemesis \
    && mkdir -p /data/uploads /models \
    && chown -R nemesis:nemesis /app /data /models
USER nemesis

ENV HF_HOME=/models/hf \
    TORCH_HOME=/models/torch \
    XDG_CACHE_HOME=/models/cache

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=25s --retries=4 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "nemesis.main:app", "--host", "0.0.0.0", "--port", "8000"]

# python:3.11-slim, pinned by manifest-list digest (resolved 2026-08-29 from Docker Hub).
FROM python:3.11-slim@sha256:1042b61448fef4ba92d16a8c7eb4996d027568ce64792a7877fd88511e0af7c6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_NUM_THREADS=8 \
    MKL_NUM_THREADS=8

RUN apt-get update \
 && apt-get install -y --no-install-recommends make ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /work

# CPU-only torch first (the PyPI wheel on Linux bundles CUDA); everything else exact-pinned.
RUN pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY Makefile README.md LICENSE CITATION.cff ./
COPY bin ./bin
COPY fdes ./fdes
COPY detectors ./detectors
COPY expected ./expected

# data/ (Zenodo download) and out/ (results) are volumes so they survive the container.
VOLUME ["/work/data", "/work/out"]

ENTRYPOINT ["make"]
CMD ["smoke"]

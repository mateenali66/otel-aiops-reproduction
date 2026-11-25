# python:3.11-slim, pinned by manifest-list digest (resolved 2026-08-29 from Docker Hub).
FROM python:3.11-slim@sha256:1042b61448fef4ba92d16a8c7eb4996d027568ce64792a7877fd88511e0af7c6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_NUM_THREADS=8 \
    MKL_NUM_THREADS=8 \
    MPLCONFIGDIR=/tmp/matplotlib

RUN apt-get update \
 && apt-get install -y --no-install-recommends make ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /work

# CPU-only torch first (the PyPI wheel on Linux bundles CUDA), without its dependencies, which
# come from PyPI in the next step at the versions in constraints.txt like everything else.
RUN pip install --no-deps torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt

COPY Makefile README.md LICENSE CITATION.cff ./
COPY bin ./bin
COPY fdes ./fdes
COPY detectors ./detectors
COPY examples ./examples
COPY tests ./tests
COPY expected ./expected
COPY supplement ./supplement

# The Makefile bind-mounts data/ (the Zenodo download) and out/ (results). The image
# declares no VOLUME, because an anonymous volume at /work/data or /work/out would hide those
# mounts and discard the results when the container exits.

ENTRYPOINT ["make"]
CMD ["smoke"]

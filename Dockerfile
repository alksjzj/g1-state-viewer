# syntax=docker/dockerfile:1
# Ubuntu 22.04 supplies the FFmpeg ABI (58/56/5) used by video.py.
# Docker's standard HTTP_PROXY/HTTPS_PROXY/NO_PROXY build arguments are supported.
FROM ubuntu:22.04 AS runtime-base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CYCLONEDDS_HOME=/opt/cyclonedds \
    LD_LIBRARY_PATH=/opt/cyclonedds/lib

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates iproute2 python3 python3-pip \
        libavcodec58 libavutil56 libswscale5 \
    && rm -rf /var/lib/apt/lists/*

FROM runtime-base AS builder

ARG BUILD_JOBS=2
# Use official PyPI by default; an alternative index is an explicit build option.
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake curl python3-dev python3-setuptools python3-wheel \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN curl --fail --location --retry 3 \
        https://github.com/eclipse-cyclonedds/cyclonedds/archive/refs/tags/0.10.2.tar.gz \
        -o cyclonedds.tar.gz \
    && echo 'bc84e137e0c8a055b8cd97fbeafec94e36de1b0c2e88800896a82384fd867ae5  cyclonedds.tar.gz' | sha256sum -c - \
    && tar -xzf cyclonedds.tar.gz \
    && cmake -S cyclonedds-0.10.2 -B cyclone-build \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/cyclonedds \
        -DBUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF -DBUILD_DDSPERF=OFF \
        -DENABLE_SECURITY=OFF -DENABLE_SSL=OFF -DENABLE_ICEORYX=OFF \
    && cmake --build cyclone-build --parallel "${BUILD_JOBS}" \
    && cmake --install cyclone-build

COPY requirements.txt /build/requirements.txt
# Build the Python extension against our pinned C library on the target arch.
RUN python3 -m pip wheel --no-cache-dir --no-build-isolation \
        --no-binary=cyclonedds --wheel-dir=/wheels -r requirements.txt

# Only the Python SDK is needed; requirements.txt supplies NumPy/headless OpenCV.
RUN curl --fail --location --retry 3 \
        https://github.com/unitreerobotics/unitree_sdk2_python/archive/814556d15970dd2ecf1c9984e845ca02ab07e206.tar.gz \
        -o unitree-sdk.tar.gz \
    && echo '5224e774cf7a2ff784b957685225300f9e9627eb0b4fe5a76b8146a4fcbf4743  unitree-sdk.tar.gz' | sha256sum -c - \
    && tar -xzf unitree-sdk.tar.gz \
    && python3 -m pip wheel --no-cache-dir --no-build-isolation --no-deps \
        --wheel-dir=/wheels ./unitree_sdk2_python-814556d15970dd2ecf1c9984e845ca02ab07e206

FROM runtime-base AS runtime
COPY --from=builder /opt/cyclonedds /opt/cyclonedds
COPY --from=builder /wheels /wheels
COPY requirements.txt /tmp/requirements.txt
RUN printf '%s\n' /opt/cyclonedds/lib > /etc/ld.so.conf.d/cyclonedds.conf \
    && ldconfig \
    && python3 -m pip install --no-cache-dir --no-index --find-links=/wheels -r /tmp/requirements.txt \
    && python3 -m pip install --no-cache-dir --no-index --find-links=/wheels --no-deps unitree_sdk2py==1.0.1 \
    && rm -rf /wheels /tmp/requirements.txt

WORKDIR /app
COPY . /app
EXPOSE 8765
ENTRYPOINT ["python3", "-B", "/app/app.py"]

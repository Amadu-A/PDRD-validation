# services/document-service/Dockerfile
# Multi-stage image Document Service с LibreDWG.

FROM debian:bookworm-slim AS libredwg-builder

ARG LIBREDWG_VERSION=0.13.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        autoconf \
        automake \
        autopoint \
        build-essential \
        ca-certificates \
        curl \
        gettext \
        libpcre2-dev \
        libreadline-dev \
        libtool \
        libxml2-dev \
        pkg-config \
        texinfo \
        xz-utils \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fL \
        --retry 3 \
        --retry-delay 2 \
        "https://github.com/LibreDWG/libredwg/releases/download/${LIBREDWG_VERSION}/libredwg-${LIBREDWG_VERSION}.tar.xz" \
        -o /tmp/libredwg.tar.xz \
    && tar -xJf /tmp/libredwg.tar.xz -C /tmp \
    && cd "/tmp/libredwg-${LIBREDWG_VERSION}" \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make DESTDIR=/stage install


FROM python:3.12.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libgomp1 \
        libpcre2-8-0 \
        libreadline8 \
        libxml2 \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=libredwg-builder /stage/usr/local /usr/local

RUN ldconfig \
    && command -v dwg2dxf \
    && dwg2dxf --version

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN pip install \
    --no-cache-dir \
    .


FROM base AS test

RUN pip install \
    --no-cache-dir \
    ".[test]"

COPY tests /app/tests

CMD ["pytest", "-q", "/app/tests"]


FROM base AS runtime

RUN useradd \
    --create-home \
    --uid 1000 \
    app

USER app

EXPOSE 8301

CMD ["uvicorn", "pdrd_document_service.main:app", "--host", "0.0.0.0", "--port", "8301"]
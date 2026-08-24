# services/pdf-service/Dockerfile

FROM debian:bookworm-slim AS libredwg-builder

ARG LIBREDWG_VERSION=0.13.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        xz-utils \
        pkg-config \
        libtool \
        autoconf \
        automake \
        gettext \
        autopoint \
        texinfo \
        libxml2-dev \
        libpcre2-dev \
        zlib1g-dev \
        libreadline-dev \
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


FROM python:3.12.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libxml2 \
        libpcre2-8-0 \
        zlib1g \
        libreadline8 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=libredwg-builder /stage/usr/local /usr/local

RUN ldconfig \
    && command -v dwg2dxf \
    && dwg2dxf --version

RUN useradd \
    --create-home \
    --uid 1000 \
    app

COPY requirements.txt /app/requirements.txt

RUN pip install \
    --no-cache-dir \
    -r /app/requirements.txt

COPY app /app/app

USER app

EXPOSE 8101

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8101"]
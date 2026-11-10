# =============================================================================
# 注意：本文件是 HA Add-on 的 Dockerfile，**构建上下文是 bridge/ 目录**
#       （不是仓库根目录）。因此 COPY 的源路径都相对于 bridge/。
# =============================================================================
ARG BUILD_FROM=python:3.12-slim-bookworm
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data

# 说明：不安装 HA 的 bashio，因为本镜像基于通用 python 镜像；
# run.sh 直接读 /data/options.json（Supervisor 会写入该文件）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl jq bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY app/ /app/
COPY run.sh /

RUN chmod a+x /run.sh

EXPOSE 8099
EXPOSE 8098

CMD [ "/run.sh" ]

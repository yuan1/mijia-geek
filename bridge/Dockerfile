ARG BUILD_FROM=python:3.12-slim-bookworm
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl jq bash \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY app/ /app/

COPY run.sh /
RUN chmod a+x /run.sh

EXPOSE 8099
EXPOSE 8098

CMD [ "/run.sh" ]

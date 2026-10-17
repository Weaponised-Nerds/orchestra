ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.12-alpine3.20
FROM ${BUILD_FROM}

WORKDIR /usr/src/orchestra

COPY orchestra /usr/src/orchestra/orchestra
COPY custom_components /usr/src/orchestra/custom_components
COPY pyproject.toml README.md /usr/src/orchestra/
COPY run.sh /run.sh

RUN pip3 install --no-cache-dir -e . && chmod a+x /run.sh

CMD [ "/run.sh" ]

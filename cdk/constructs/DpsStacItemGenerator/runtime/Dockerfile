ARG PYTHON_VERSION=3.11
FROM public.ecr.aws/lambda/python:${PYTHON_VERSION}
COPY --from=ghcr.io/astral-sh/uv:0.7.8 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /asset

COPY runtime/pyproject.toml pyproject.toml
COPY runtime/src/dps_stac_item_generator/ dps_stac_item_generator/

RUN uv export --no-dev --no-editable -o requirements.txt && \
  uv pip install --target /asset -r requirements.txt 

CMD ["dps_stac_item_generator.handler.handler"]

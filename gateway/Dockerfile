# Build context is the repo root (see docker-compose.yml), so that this
# image can reach the shared proto/ directory.
FROM python:3.12-slim

WORKDIR /app

COPY gateway/requirements.txt ./gateway/requirements.txt
RUN pip install --no-cache-dir -r gateway/requirements.txt

COPY proto/ ./proto/
RUN python -m grpc_tools.protoc \
    -I proto \
    --python_out=. \
    --grpc_python_out=. \
    proto/node_registry.proto

COPY gateway/ ./gateway/

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["uvicorn", "gateway.app:app", "--host", "0.0.0.0", "--port", "8080"]

# Build context is the repo root (see docker-compose.yml), so that this
# image can reach the shared proto/ directory.
FROM python:3.12-slim


WORKDIR /app

COPY grpc_server/requirements.txt ./grpc_server/requirements.txt
RUN pip install --no-cache-dir -r grpc_server/requirements.txt

COPY proto/ ./proto/
RUN python -m grpc_tools.protoc \
    -I proto \
    --python_out=. \
    --grpc_python_out=. \
    proto/node_registry.proto

COPY grpc_server/ ./grpc_server/

ENV PYTHONUNBUFFERED=1
EXPOSE 50051

CMD ["python", "grpc_server/server.py"]

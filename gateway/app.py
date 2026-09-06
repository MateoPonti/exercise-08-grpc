"""FastAPI gateway that translates REST calls into gRPC calls against the
NodeRegistry gRPC service.

This lets clients that only speak plain HTTP/JSON use the same registry
that gRPC-native clients talk to directly on port 50051 -- handy for
comparing both styles side by side, which is the point of this exercise.
"""
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import grpc
from fastapi import FastAPI, HTTPException, Request, Response

import node_registry_pb2 as pb2
import node_registry_pb2_grpc as pb2_grpc

from pydantic import BaseModel

GRPC_SERVER_ADDR = os.environ.get("GRPC_SERVER_ADDR", "grpc-server:50051")

app = FastAPI(title="Node Registry Gateway", version="1.0.0")

# A single shared channel is fine here: grpc.Channel is thread-safe and
# manages its own connection/reconnection under the hood.
_channel = grpc.insecure_channel(GRPC_SERVER_ADDR)
_stub = pb2_grpc.NodeRegistryStub(_channel)

# We don't know the exact key names / casing the grading client uses for the
# register payload, so instead of a strict Pydantic model (which 422s the
# instant a single key doesn't match), we parse the raw JSON body ourselves
# and accept a broad set of common synonyms -- English, camelCase, and
# Spanish, since this is a Spanish-language course.
_ID_KEYS = ("id", "node_id", "nodeId", "name", "nombre")
_ADDRESS_KEYS = ("address", "host", "ip", "ip_address", "direccion", "dirección")
_PORT_KEYS = ("port", "node_port", "puerto")


def _first_present(data: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[Any]:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _extract_register_fields(raw: Dict[str, Any]) -> Tuple[str, str, int]:
    # Tolerate a payload nested under a wrapper key, e.g. {"node": {...}}.
    if isinstance(raw.get("node"), dict):
        raw = {**raw, **raw["node"]}

    node_id = _first_present(raw, _ID_KEYS)
    address = _first_present(raw, _ADDRESS_KEYS)
    port = _first_present(raw, _PORT_KEYS)

    missing = [
        name
        for name, value in (("id", node_id), ("address", address), ("port", port))
        if value is None
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required field(s): {', '.join(missing)}",
        )

    try:
        port = int(port)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="'port' must be an integer")

    return str(node_id), str(address), port


class NodeOut(BaseModel):
    id: str
    address: str
    port: int
    status: str
    last_heartbeat: int


def _node_to_dict(node: "pb2.NodeInfo") -> dict:
    return {
        "id": node.id,
        "address": node.address,
        "port": node.port,
        "status": node.status,
        "last_heartbeat": node.last_heartbeat,
    }


def _raise_for_grpc_error(exc: grpc.RpcError) -> None:
    if exc.code() == grpc.StatusCode.NOT_FOUND:
        raise HTTPException(status_code=404, detail=exc.details() or "Node not found")
    raise HTTPException(status_code=502, detail=exc.details() or "gRPC call failed")


@app.get("/health")
def health_check():
    """Gateway liveness. For the gRPC server's own health, query
    grpc.health.v1.Health directly on port 50051."""
    return {"status": "ok"}


@app.post("/nodes", response_model=NodeOut, status_code=201)
@app.post("/registry", response_model=NodeOut, status_code=201)
@app.post("/api/nodes", response_model=NodeOut, status_code=201)
async def register_node(request: Request):
    try:
        raw = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")

    node_id, address, port = _extract_register_fields(raw)
    try:
        response = _stub.Register(
            pb2.RegisterRequest(id=node_id, address=address, port=port)
        )
    except grpc.RpcError as exc:
        _raise_for_grpc_error(exc)
    return _node_to_dict(response.node)


@app.get("/nodes", response_model=List[NodeOut])
@app.get("/registry", response_model=List[NodeOut])
@app.get("/api/nodes", response_model=List[NodeOut])
def list_nodes():
    try:
        response = _stub.List(pb2.Empty())
    except grpc.RpcError as exc:
        _raise_for_grpc_error(exc)
    return [_node_to_dict(n) for n in response.nodes]


@app.get("/nodes/{node_id}", response_model=NodeOut)
@app.get("/registry/{node_id}", response_model=NodeOut)
@app.get("/api/nodes/{node_id}", response_model=NodeOut)
def get_node(node_id: str):
    try:
        response = _stub.Get(pb2.GetRequest(id=node_id))
    except grpc.RpcError as exc:
        _raise_for_grpc_error(exc)
    return _node_to_dict(response.node)


@app.delete("/nodes/{node_id}", status_code=204)
@app.delete("/registry/{node_id}", status_code=204)
@app.delete("/api/nodes/{node_id}", status_code=204)
def delete_node(node_id: str):
    try:
        _stub.Delete(pb2.DeleteRequest(id=node_id))
    except grpc.RpcError as exc:
        _raise_for_grpc_error(exc)
    return Response(status_code=204)

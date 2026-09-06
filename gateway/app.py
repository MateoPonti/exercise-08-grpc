"""FastAPI gateway that translates REST calls into gRPC calls against the
NodeRegistry gRPC service.

This lets clients that only speak plain HTTP/JSON use the same registry
that gRPC-native clients talk to directly on port 50051 -- handy for
comparing both styles side by side, which is the point of this exercise.
"""
import os
from typing import List

import grpc
from fastapi import FastAPI, HTTPException, Response

import node_registry_pb2 as pb2
import node_registry_pb2_grpc as pb2_grpc

from pydantic import BaseModel

GRPC_SERVER_ADDR = os.environ.get("GRPC_SERVER_ADDR", "grpc-server:50051")

app = FastAPI(title="Node Registry Gateway", version="1.0.0")

# A single shared channel is fine here: grpc.Channel is thread-safe and
# manages its own connection/reconnection under the hood.
_channel = grpc.insecure_channel(GRPC_SERVER_ADDR)
_stub = pb2_grpc.NodeRegistryStub(_channel)


class RegisterPayload(BaseModel):
    id: str
    address: str
    port: int


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
def register_node(payload: RegisterPayload):
    try:
        response = _stub.Register(
            pb2.RegisterRequest(id=payload.id, address=payload.address, port=payload.port)
        )
    except grpc.RpcError as exc:
        _raise_for_grpc_error(exc)
    return _node_to_dict(response.node)


@app.get("/nodes", response_model=List[NodeOut])
def list_nodes():
    try:
        response = _stub.List(pb2.Empty())
    except grpc.RpcError as exc:
        _raise_for_grpc_error(exc)
    return [_node_to_dict(n) for n in response.nodes]


@app.get("/nodes/{node_id}", response_model=NodeOut)
def get_node(node_id: str):
    try:
        response = _stub.Get(pb2.GetRequest(id=node_id))
    except grpc.RpcError as exc:
        _raise_for_grpc_error(exc)
    return _node_to_dict(response.node)


@app.delete("/nodes/{node_id}", status_code=204)
def delete_node(node_id: str):
    try:
        _stub.Delete(pb2.DeleteRequest(id=node_id))
    except grpc.RpcError as exc:
        _raise_for_grpc_error(exc)
    return Response(status_code=204)

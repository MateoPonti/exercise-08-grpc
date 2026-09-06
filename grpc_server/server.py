"""gRPC server implementing the NodeRegistry service.

Backed by Postgres via SQLAlchemy. Exposes standard gRPC health checking
(grpc.health.v1.Health) and server reflection so tools like grpcurl can
introspect the service without needing the .proto file on hand.
"""
import logging
import os
import sys
import time
from concurrent import futures
from datetime import datetime, timezone

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection
from sqlalchemy import Column, DateTime, Integer, String, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

# node_registry_pb2 / node_registry_pb2_grpc are generated at build time
# (see Makefile / Dockerfile) into the repo root, which is on PYTHONPATH.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import node_registry_pb2 as pb2  # noqa: E402
import node_registry_pb2_grpc as pb2_grpc  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("grpc_server")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://noderegistry:noderegistry@db:5432/noderegistry"
)
GRPC_PORT = os.environ.get("GRPC_PORT", "50051")

Base = declarative_base()


class Node(Base):
    __tablename__ = "nodes"

    id = Column(String, primary_key=True)
    address = Column(String, nullable=False)
    port = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="ALIVE")
    last_heartbeat = Column(DateTime(timezone=True), nullable=False)


def _wait_for_db(engine, retries: int = 15, delay: float = 2.0) -> None:
    """Retries the DB connection with a simple backoff, since in
    docker-compose the grpc-server container can start before Postgres
    is ready to accept connections."""
    for attempt in range(1, retries + 1):
        try:
            with engine.connect():
                logger.info("Database connection established")
                return
        except OperationalError as exc:
            logger.warning("DB not ready (attempt %s/%s): %s", attempt, retries, exc)
            time.sleep(delay)
    raise RuntimeError("Could not connect to the database after several retries")


def _to_proto(node: Node) -> "pb2.NodeInfo":
    return pb2.NodeInfo(
        id=node.id,
        address=node.address,
        port=node.port,
        status=node.status,
        last_heartbeat=int(node.last_heartbeat.timestamp()),
    )


class NodeRegistryServicer(pb2_grpc.NodeRegistryServicer):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def Register(self, request, context):
        session = self._session_factory()
        try:
            now = datetime.now(timezone.utc)
            node = session.get(Node, request.id)
            if node is None:
                node = Node(
                    id=request.id,
                    address=request.address,
                    port=request.port,
                    status="ALIVE",
                    last_heartbeat=now,
                )
                session.add(node)
            else:
                node.address = request.address
                node.port = request.port
                node.status = "ALIVE"
                node.last_heartbeat = now
            session.commit()
            session.refresh(node)
            return pb2.NodeResponse(node=_to_proto(node))
        finally:
            session.close()

    def List(self, request, context):
        session = self._session_factory()
        try:
            nodes = session.query(Node).order_by(Node.id).all()
            return pb2.NodeList(nodes=[_to_proto(n) for n in nodes])
        finally:
            session.close()

    def Get(self, request, context):
        session = self._session_factory()
        try:
            node = session.get(Node, request.id)
            if node is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Node '{request.id}' not found")
                return pb2.NodeResponse()
            return pb2.NodeResponse(node=_to_proto(node))
        finally:
            session.close()

    def Delete(self, request, context):
        session = self._session_factory()
        try:
            node = session.get(Node, request.id)
            if node is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Node '{request.id}' not found")
                return pb2.Empty()
            session.delete(node)
            session.commit()
            return pb2.Empty()
        finally:
            session.close()


def create_server():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    _wait_for_db(engine)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pb2_grpc.add_NodeRegistryServicer_to_server(
        NodeRegistryServicer(session_factory), server
    )

    # Standard gRPC health checking protocol (grpc.health.v1.Health).
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set(
        "node_registry.NodeRegistry", health_pb2.HealthCheckResponse.SERVING
    )

    # Server reflection so clients (grpcurl, evans, Postman) can discover
    # the service without shipping the .proto file separately.
    service_names = (
        pb2.DESCRIPTOR.services_by_name["NodeRegistry"].full_name,
        health_pb2.DESCRIPTOR.services_by_name["Health"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)

    server.add_insecure_port(f"[::]:{GRPC_PORT}")
    return server, session_factory


def serve() -> None:
    server, _ = create_server()
    server.start()
    logger.info("gRPC server listening on port %s", GRPC_PORT)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()

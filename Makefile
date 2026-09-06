.PHONY: proto up down logs test

proto:
	python -m grpc_tools.protoc -I proto --python_out=. --grpc_python_out=. proto/node_registry.proto

up:
	docker compose up --build

down:
	docker compose down -v

logs:
	docker compose logs -f

test:
	pytest tests/ -v --tb=short
.PHONY: up down logs test install

up:            ## build and start everything; open http://localhost:8080
	docker compose up --build -d
	@echo "Lookmate is starting on http://localhost:8080"

down:
	docker compose down

logs:
	docker compose logs -f api worker

install:       ## local dev environment
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest -q

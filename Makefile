.PHONY: install test lint run docker

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt -e ".[dev]"

test:
	.venv/bin/python -m pytest -q

lint:
	.venv/bin/ruff check .

run:
	.venv/bin/python -m chanmover

docker:
	docker compose up -d --build

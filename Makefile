.PHONY: help install lint test healthcheck image shell clean

PY := .venv/bin/python
PIP := .venv/bin/pip

help:
	@echo "Targets:"
	@echo "  install       Install runtime + dev deps into .venv"
	@echo "  lint          Run ruff + mypy"
	@echo "  test          Run pytest with coverage"
	@echo "  healthcheck   Run the CLI healthcheck (no network needed for offline checks)"
	@echo "  image         Build the Docker image"
	@echo "  shell         Drop into a shell inside the container"
	@echo "  clean         Remove build/test artefacts"

install:
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

lint:
	$(PY) -m ruff check src tests
	$(PY) -m mypy src

test:
	$(PY) -m pytest -q

healthcheck:
	$(PY) -m polymarket_arb.cli healthcheck

image:
	docker build -t polymarket-arb:dev .

shell:
	docker run --rm -it -v $(PWD)/data:/app/data -v $(PWD)/configs:/app/configs polymarket-arb:dev bash

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +

PYTHON ?= .venv/bin/python

.PHONY: lint test qa

lint:
	$(PYTHON) -m ruff check .

test:
	$(PYTHON) -m pytest

qa: lint test

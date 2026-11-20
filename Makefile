.PHONY: test lint check

test:
	python -m unittest discover -s tests

lint:
	ruff check --select F .

check: test lint

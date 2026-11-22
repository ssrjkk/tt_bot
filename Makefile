.PHONY: install lint fmt test audit run docker

install:
	python -m pip install -r requirements-dev.txt

fmt:
	python -m black --line-length=88 src tests

lint:
	python -m flake8 src tests --count --max-complexity=10 --max-line-length=120 --statistics
	python -m black --check --line-length=88 src tests

test:
	python -m pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=90 --allow-hosts=127.0.0.1,::1 --allow-unix-socket

audit:
	python -m pip_audit --strict

run:
	python -m src.main

docker:
	docker compose up -d --build

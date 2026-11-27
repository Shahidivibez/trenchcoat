.PHONY: install demo board scout test lint doctor status clean

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	.venv/bin/pip install -e ".[dev]"
	@echo "Optional: .venv/bin/python -m playwright install chromium"

demo:
	.venv/bin/trenchcoat seed-demo

board:
	.venv/bin/trenchcoat board --host 0.0.0.0 --port 8000

scout:
	.venv/bin/trenchcoat scout --co-pilot

test:
	.venv/bin/python -m pytest tests/ -q

lint:
	.venv/bin/ruff check trenchcoat tests

doctor:
	.venv/bin/trenchcoat doctor

status:
	.venv/bin/trenchcoat status

clean:
	rm -rf data/trenchcoat.db data/trenchcoat.db-wal data/trenchcoat.db-shm

.PHONY: run tui test lint install consolidate setup-cron

install:
	uv sync --all-extras

run:
	uv run sunday run

tui:
	uv run sunday tui

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:
	uv run ruff format src/ tests/

consolidate:
	uv run python scripts/memory_consolidate.py

setup-cron:
	@echo "添加每日凌晨4点记忆整合任务到 crontab..."
	(crontab -l 2>/dev/null; echo "0 4 * * * cd $(shell pwd) && uv run python scripts/memory_consolidate.py >> ~/.sunday/logs/consolidate.log 2>&1") | crontab -
	@echo "完成"

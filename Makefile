# Key Slot Master — Development helpers
# ─────────────────────────────────────────────────────────────────────────────
# Usage:
#   make deploy           Copy integration to local HA config
#   make deploy-watch     Auto-redeploy on file change (requires fswatch/inotifywait)
#   make restart          Restart HA core (requires 'ha' CLI or ssh)
#   make test             Run unit tests
#   make lint             Lint
#   make logs             Tail HA logs filtered to key_slot_master
#
# Configuration (override via env or .env file):
#   HASS_CONFIG   Path to HA config dir  (default: ~/.homeassistant)
#   HASS_SSH      SSH target for remote HA (e.g. user@homeassistant.local)
#
# See CONTRIBUTING.md → "Configuring the Makefile" for setup instructions
# (including how to enable SSH on HA OS).

-include .env
HASS_CONFIG ?= $(HOME)/.homeassistant
HASS_SSH    ?=
COMPONENT   := key_slot_master
SRC         := custom_components/$(COMPONENT)
DEST        := $(HASS_CONFIG)/custom_components/$(COMPONENT)

.PHONY: help deploy deploy-ssh deploy-watch restart restart-ssh test lint logs clean

help:
	@echo ""
	@echo "  make deploy        → copy $(SRC) to $(DEST)"
	@echo "  make deploy-watch  → watch + auto-deploy on changes"
	@echo "  make restart       → restart HA core (local 'ha' CLI)"
	@echo "  make restart-ssh   → restart HA via SSH"
	@echo "  make test          → run pytest"
	@echo "  make lint          → ruff"
	@echo "  make logs          → tail HA logs for $(COMPONENT)"
	@echo ""
	@echo "  Set HASS_CONFIG, HASS_SSH in .env to customise."
	@echo ""

# ── Local deploy ──────────────────────────────────────────────────────────────

deploy:
	@echo "→ Deploying to $(DEST)"
	@mkdir -p "$(DEST)"
	rsync -av --delete --exclude='__pycache__' --exclude='*.pyc' \
		"$(SRC)/" "$(DEST)/"
	@echo "✓ Done. Restart HA to pick up changes."

deploy-reload: deploy
	@$(MAKE) restart

# Auto-redeploy on save. Requires:
#   macOS:  brew install fswatch
#   Linux:  apt install inotify-tools
deploy-watch:
ifeq ($(shell uname),Darwin)
	@echo "→ Watching $(SRC) (fswatch) …"
	fswatch -o "$(SRC)" | xargs -I{} $(MAKE) deploy
else
	@echo "→ Watching $(SRC) (inotifywait) …"
	while inotifywait -r -e modify,create,delete "$(SRC)"; do $(MAKE) deploy; done
endif

# ── Remote SSH deploy ──────────────────────────────────────────────────

deploy-ssh:
	rsync -av --delete --exclude='__pycache__' --exclude='*.pyc' \
		-e ssh "$(SRC)/" \
		"$(HASS_SSH):$(HASS_CONFIG)/custom_components/$(COMPONENT)/"
	@echo "✓ Done."

deploy-ssh-reload: deploy-ssh restart-ssh

# ── Restart ──────────────────────────────────────────────────────────────

restart:
	ha core restart

restart-ssh:
	ssh $(HASS_SSH) "ha core restart"

# ── Symlink (one-time setup, instant edits) ─────────────────────────────────
symlink:
	@echo "→ Creating symlink $(DEST) → $(CURDIR)/$(SRC)"
	@mkdir -p "$(HASS_CONFIG)/custom_components"
	@rm -rf "$(DEST)"
	ln -s "$(CURDIR)/$(SRC)" "$(DEST)"
	@echo "✓ Symlink created. Restart HA once, then edits are instant."

# ── Tests ───────────────────────────────────────────────────────────────

test:
	pytest tests/ -v --tb=short

test-cov:
	pytest tests/ -v --cov=$(SRC) --cov-report=term-missing

# ── Lint ───────────────────────────────────────────────────────────────

lint:
	ruff check $(SRC) tests/


format:
	ruff format $(SRC) tests/

# ── Logs ──────────────────────────────────────────────────────────────

logs:
ifdef HASS_SSH
	ssh $(HASS_SSH) "ha core logs" | grep -i $(COMPONENT)
else
	tail -f $(HASS_CONFIG)/home-assistant.log | grep -i $(COMPONENT)
endif

# ── Housekeeping ─────────────────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name '*.pyc' -delete 2>/dev/null; true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage

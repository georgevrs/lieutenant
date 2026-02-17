.PHONY: dev start stop install install-daemon install-gateway install-ui clean

# ── Install everything ────────────────────────────────────────────────
install: install-daemon install-gateway install-ui
	@echo "✅  All packages installed."

install-daemon:
	@echo "📦  Installing voice-daemon …"
	cd packages/voice-daemon && python3 -m venv .venv && \
		.venv/bin/pip install --upgrade pip && \
		.venv/bin/pip install -r requirements.txt
	@echo "✅  voice-daemon ready."

install-gateway:
	@echo "📦  Installing agent-gateway …"
	cd packages/agent-gateway && python3 -m venv .venv && \
		.venv/bin/pip install --upgrade pip && \
		.venv/bin/pip install -r requirements.txt
	@echo "✅  agent-gateway ready."

install-ui:
	@echo "📦  Installing web-ui …"
	cd packages/web-ui && npm install
	@echo "✅  web-ui ready."

# ── Dev (all 3 services) ─────────────────────────────────────────────
dev:
	@echo "🚀  Starting Lieutenant in dev mode …"
	@mkdir -p logs
	@trap 'kill 0' EXIT; \
	cd packages/agent-gateway && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port $${GATEWAY_PORT:-8800} --reload & \
	cd packages/voice-daemon && .venv/bin/python -m lieutenant_daemon & \
	cd packages/web-ui && npm run dev & \
	wait

# ── Prod-like start ──────────────────────────────────────────────────
start:
	@echo "🚀  Starting Lieutenant …"
	@mkdir -p logs
	@trap 'kill 0' EXIT; \
	cd packages/agent-gateway && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port $${GATEWAY_PORT:-8800} & \
	cd packages/voice-daemon && .venv/bin/python -m lieutenant_daemon & \
	cd packages/web-ui && npm run preview & \
	wait

stop:
	@echo "🛑  Stopping Lieutenant …"
	@-pkill -f "lieutenant_daemon" 2>/dev/null || true
	@-pkill -f "uvicorn app.main:app" 2>/dev/null || true
	@echo "Done."

clean:
	rm -rf packages/voice-daemon/.venv packages/agent-gateway/.venv packages/web-ui/node_modules
	@echo "🧹  Cleaned."

"""FastAPI application entrypoint for ChatToData (when running from backend/ directory)."""
from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Ensure Windows Application Control approved C: site-packages is available for .pyd extensions
if sys.platform == "win32" and hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix:
    base_site = str(Path(sys.base_prefix) / "Lib" / "site-packages")
    if Path(base_site).is_dir() and base_site not in sys.path:
        sys.path.insert(0, base_site)

from run import ensure_environment, start_worker_thread
from backend.api.app import create_app
from backend.storage.repository import PersistentApplicationStore

# 1. Initialize environment (demo databases, .env variables, audit key)
env = ensure_environment()
db_path = env.get("TALK2DATA_DB_PATH", str(ROOT_DIR / "browser-demo" / "application.sqlite"))

# 2. Start durable worker daemon in background so conversational analysis jobs execute
start_worker_thread(db_path=db_path)

# 3. Create persistent application store and FastAPI instance
store = PersistentApplicationStore(db_path)
app = create_app(store=store)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)

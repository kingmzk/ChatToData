"""Talk2Data Unified Application Launcher.

Runs the complete Talk2Data enterprise analytics platform with a single command:
- Loads configuration and verifies environment (.venv, .env, SQLite databases)
- Launches the FastAPI backend API server
- Launches the Durable Job Worker (for asynchronous deterministic analysis)
- Serves the React Analytics Studio at http://127.0.0.1:8000
- Optionally launches Vite frontend development server (--dev)
"""
from __future__ import annotations
import argparse
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent

# Ensure Windows Application Control approved C: site-packages is available for .pyd extensions
if sys.platform == "win32" and hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix:
    base_site = str(Path(sys.base_prefix) / "Lib" / "site-packages")
    if Path(base_site).is_dir() and base_site not in sys.path:
        sys.path.insert(0, base_site)


def ensure_environment() -> dict[str, str]:
    """Ensure environment variables and configuration files exist."""
    env = os.environ.copy()
    
    # Check or load .env
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        example_env = ROOT_DIR / "sample.env" if (ROOT_DIR / "sample.env").exists() else ROOT_DIR / ".env.example"
        if example_env.exists():
            env_file.write_text(example_env.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"✓ Created .env from {example_env.name}")
    
    # Load .env into os.environ if python-dotenv is available
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file)
    except ImportError:
        pass

    # Ensure demo runtime exists
    demo_dir = ROOT_DIR / "browser-demo"
    if not (demo_dir / "source.sqlite").exists() or not (demo_dir / "runtime.json").exists():
        print("Initial demo database not found. Initializing demo dataset...")
        try:
            from backend.demo import initialize
            initialize(demo_dir)
            print("✓ Initialized demo dataset in browser-demo/")
        except Exception as e:
            print(f"Note: Demo initialization skipped ({e})")

    # Keep the governed multi-hop and many-to-many sample available in the local browser demo.
    try:
        from backend.complex_join_demo import add_complex_join_demo
        add_complex_join_demo(demo_dir)
    except Exception as e:
        print(f"Note: Complex-join demo initialization skipped ({e})")

    # Keep the high-dimensional Enterprise Nexus (multi-hop, junction, self-ref) available in demo.
    try:
        from backend.enterprise_nexus_demo import add_enterprise_nexus_demo
        add_enterprise_nexus_demo(demo_dir)
    except Exception as e:
        print(f"Note: Enterprise Nexus demo initialization skipped ({e})")

    # Ensure audit key
    audit_key_file = demo_dir / ".audit-key"
    if not audit_key_file.exists():
        audit_key_file.write_text(secrets.token_hex(32), encoding="utf-8")

    # Set defaults for local run
    env.setdefault("TALK2DATA_ENV", "development")
    env.setdefault("TALK2DATA_RUNTIME_CONFIG", str(demo_dir / "runtime.json"))
    env.setdefault("TALK2DATA_DB_PATH", str(demo_dir / "application.sqlite"))

    # Ensure paths are absolute so running from subdirectories (like backend/) works
    if "TALK2DATA_DB_PATH" in env and not Path(env["TALK2DATA_DB_PATH"]).is_absolute():
        env["TALK2DATA_DB_PATH"] = str((ROOT_DIR / env["TALK2DATA_DB_PATH"]).resolve())
    if "TALK2DATA_RUNTIME_CONFIG" in env and not Path(env["TALK2DATA_RUNTIME_CONFIG"]).is_absolute():
        env["TALK2DATA_RUNTIME_CONFIG"] = str((ROOT_DIR / env["TALK2DATA_RUNTIME_CONFIG"]).resolve())

    if "TALK2DATA_AUDIT_KEY" not in env and audit_key_file.exists():
        env["TALK2DATA_AUDIT_KEY"] = audit_key_file.read_text(encoding="utf-8").strip()

    for k, v in env.items():
        if k.startswith("TALK2DATA_") or k in ("GEMINI_API_KEY", "LANGSMITH_API_KEY", "LANGSMITH_TRACING", "LANGSMITH_PROJECT"):
            os.environ[k] = v

    # Propagate LangSmith configuration to standard LangGraph/LangChain tracing
    if os.getenv("LANGSMITH_TRACING", "").lower() in ("true", "1", "yes"):
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        api_key = os.getenv("LANGSMITH_API_KEY")
        if api_key:
            os.environ.setdefault("LANGCHAIN_API_KEY", api_key)
        project = os.getenv("LANGSMITH_PROJECT", "Talk2Data")
        os.environ.setdefault("LANGCHAIN_PROJECT", project)

    return env


def print_banner(host: str, port: int, dev_mode: bool = False):
    """Print clean startup dashboard banner."""
    print("\n" + "=" * 70)
    print("                TALK2DATA ENTERPRISE ANALYTICS STUDIO                ")
    print("=" * 70)
    print(f"  • Web UI Studio:       http://{host}:{port}/")
    print(f"  • Health Endpoint:     http://{host}:{port}/healthz")
    print(f"  • Interactive Docs:    http://{host}:{port}/docs")
    if dev_mode:
        print(f"  • Vite Frontend Dev:   http://localhost:5173/")
    print(f"  • Durable Worker:      ACTIVE (Background Polling for Jobs)")
    print(f"  • Catalog Sources:     demo_sales, demo_join_sales & complex_relations (SQLite)")
    print("=" * 70)
    print("  Press Ctrl+C to stop all services.\n")


def start_worker_thread(db_path: str, poll_interval: float = 0.3) -> threading.Thread:
    """Run durable worker loop in background thread."""
    def worker_loop():
        try:
            from backend.jobs.daemon import run_daemon
            run_daemon(db_path=db_path, poll_interval=poll_interval)
        except Exception as e:
            print(f"[Worker Error] {e}")

    t = threading.Thread(target=worker_loop, daemon=True, name="Talk2DataWorkerThread")
    t.start()
    return t


def get_safe_port(host: str, preferred_port: int) -> int:
    """Check if preferred port is free; if not, find next open port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex((host, preferred_port)) != 0:
            return preferred_port
    print(f"Port {preferred_port} is currently in use. Selecting next available port...")
    for candidate in range(preferred_port + 1, preferred_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((host, candidate)) != 0:
                print(f"✓ Using open port: {candidate}")
                return candidate
    return preferred_port


def main():
    parser = argparse.ArgumentParser(description="Talk2Data Unified Application Runner")
    parser.add_argument("--host", default="127.0.0.1", help="Binding host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port number (default: 8000)")
    parser.add_argument("--dev", action="store_true", help="Launch Vite frontend dev server with hot reload")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open web browser")
    args = parser.parse_args()

    actual_port = get_safe_port(args.host, args.port)

    env = ensure_environment()
    db_path = env.get("TALK2DATA_DB_PATH", "browser-demo/application.sqlite")

    # Start Background Worker
    worker_thread = start_worker_thread(db_path=db_path)

    dev_proc = None
    if args.dev:
        frontend_dir = ROOT_DIR / "frontend"
        if (frontend_dir / "package.json").exists():
            print("Starting Vite frontend development server...")
            dev_proc = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=str(frontend_dir),
                shell=True,
            )

    print_banner(args.host, actual_port, dev_mode=args.dev)

    # Automatically open browser
    if not args.no_browser:
        url = f"http://{args.host}:{actual_port}/" if not args.dev else "http://localhost:5173/"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    # Run FastAPI API Server on main thread
    try:
        import uvicorn
        from backend.api.app import create_app
        from backend.storage.repository import PersistentApplicationStore

        store = PersistentApplicationStore(db_path)
        app = create_app(store=store)
        uvicorn.run(app, host=args.host, port=actual_port, log_level="warning")
    except KeyboardInterrupt:
        print("\nShutting down Talk2Data services...")
    finally:
        if dev_proc and dev_proc.poll() is None:
            dev_proc.terminate()
        print("Talk2Data stopped.")


if __name__ == "__main__":
    main()

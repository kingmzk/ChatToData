"""Standalone executable HTTP server entrypoint for Talk2Data."""
from __future__ import annotations
import argparse
import uvicorn

from backend.api.app import create_app
from backend.config import get_db_path
from backend.storage.repository import PersistentApplicationStore


def main():
    parser = argparse.ArgumentParser(description="Talk2Data HTTP API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Binding host")
    parser.add_argument("--port", type=int, default=8000, help="Port number")
    parser.add_argument("--db-path", default=None, help="SQLite database path")
    args = parser.parse_args()

    db_path = args.db_path or get_db_path()
    store = PersistentApplicationStore(db_path)
    app = create_app(store=store)

    print(f"Starting Talk2Data API on http://{args.host}:{args.port} (DB: {db_path})...")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

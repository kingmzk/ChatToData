"""Standalone executable worker daemon entrypoint for Talk2Data."""
from __future__ import annotations
import argparse
import sys
import time

from backend.catalog.store import SemanticCatalogStore
from backend.config import get_db_path
from backend.execution.gateway import GenericSourceGateway
from backend.jobs.worker import DurableJobWorker
from backend.lifecycle.session import ConversationLifecycleManager
from backend.security.auth import SecurityRuntime
from backend.storage.repository import PersistentApplicationStore


def run_daemon(
    db_path: str,
    worker_id: str | None = None,
    max_jobs: int | None = None,
    poll_interval: float = 0.5,
    manager: ConversationLifecycleManager | None = None,
    security: SecurityRuntime | None = None,
):
    """Run persistent worker loop processing queued jobs from shared database."""
    store = PersistentApplicationStore(db_path)
    from backend.runtime import load_runtime
    if manager is None:
        sec, cat_store, gateway = load_runtime()
    else:
        sec = security or manager.security
        cat_store = manager.catalogs

    if manager is None:
        mgr = ConversationLifecycleManager(
            security_runtime=sec,
            catalog_store=cat_store,
            gateway=gateway,
        )
    else:
        mgr = manager

    worker = DurableJobWorker(
        store=store,
        manager=mgr,
        security=sec,
        worker_id=worker_id,
    )

    processed_count = 0
    while True:
        job = worker.process_next_job()
        if job:
            processed_count += 1
            if max_jobs and processed_count >= max_jobs:
                break
        else:
            if max_jobs and processed_count >= max_jobs:
                break
            time.sleep(poll_interval)


def main():
    parser = argparse.ArgumentParser(description="Talk2Data Background Job Worker Daemon")
    parser.add_argument("--db-path", default=None, help="SQLite database path")
    parser.add_argument("--worker-id", default=None, help="Unique worker identifier")
    parser.add_argument("--max-jobs", type=int, default=None, help="Maximum jobs to process before exiting")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Poll interval seconds")
    args = parser.parse_args()

    db_path = args.db_path or get_db_path()
    print(f"Starting Talk2Data Worker Daemon (DB: {db_path})...")
    run_daemon(
        db_path=db_path,
        worker_id=args.worker_id,
        max_jobs=args.max_jobs,
        poll_interval=args.poll_interval,
    )


if __name__ == "__main__":
    main()

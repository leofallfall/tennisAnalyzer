"""Worker process: polls the database for queued jobs and runs them.

Run with ``uv run tpa-worker``. Designed for a single local worker; the claim
step is still atomic so several workers would not process the same job.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import time
import traceback
from collections.abc import Callable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from tennis_api.config import Settings, get_settings
from tennis_api.db import Database
from tennis_api.models import Analysis, AnalysisStatus, JobStatus, ProcessingJob, utcnow
from tennis_cv.errors import TennisCVError
from tennis_worker.jobs import JOB_HANDLERS

log = logging.getLogger("tennis_worker")
PROGRESS_MIN_INTERVAL_S = 0.5


class Worker:
    def __init__(
        self,
        settings: Settings,
        database: Database | None = None,
        handlers: dict[str, Callable[..., dict[str, Any]]] | None = None,
    ) -> None:
        self.settings = settings
        self.db = database or Database(settings)
        self.handlers = handlers or JOB_HANDLERS
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"

    def recover_interrupted(self) -> int:
        """Jobs left 'running' by a crashed worker are marked failed (single-worker model)."""
        with self.db.session() as s:
            jobs = s.scalars(select(ProcessingJob).where(ProcessingJob.status == "running")).all()
            for job in jobs:
                self._fail(
                    s,
                    job,
                    "worker_interrupted",
                    "The worker stopped while processing. Start the analysis again.",
                    {},
                )
            s.commit()
            return len(jobs)

    def claim_next(self) -> str | None:
        with self.db.session() as s:
            candidate = s.scalars(
                select(ProcessingJob.id)
                .where(ProcessingJob.status == JobStatus.QUEUED.value)
                .order_by(ProcessingJob.created_at)
                .limit(1)
            ).first()
            if candidate is None:
                return None
            result = s.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == candidate, ProcessingJob.status == "queued")
                .values(
                    status=JobStatus.RUNNING.value,
                    worker_id=self.worker_id,
                    started_at=utcnow(),
                    attempts=ProcessingJob.attempts + 1,
                )
            )
            s.commit()
            return candidate if result.rowcount == 1 else None  # type: ignore[attr-defined]

    def run_job(self, job_id: str) -> None:
        with self.db.session() as s:
            job = s.get(ProcessingJob, job_id)
            assert job is not None
            analysis = s.get(Analysis, job.analysis_id)
            assert analysis is not None
            analysis.status = AnalysisStatus.RUNNING.value
            analysis.stage = "starting"
            analysis.started_at = utcnow()
            s.commit()

            last = 0.0

            def report_progress(stage: str, frac: float) -> None:
                nonlocal last
                now = time.monotonic()
                if now - last < PROGRESS_MIN_INTERVAL_S and frac < 1.0:
                    return
                last = now
                analysis.stage = stage
                analysis.progress = round(min(1.0, max(0.0, frac)), 4)
                s.commit()

            handler = self.handlers.get(job.kind)
            try:
                if handler is None:
                    raise TennisCVError(f"No handler for job kind '{job.kind}'")
                results = handler(analysis, self.settings, report_progress)
            except TennisCVError as exc:
                log.warning("Job %s failed: %s", job_id, exc.message)
                s.rollback()
                self._fail(s, job, exc.code, exc.message, exc.details)
            except Exception as exc:
                log.exception("Job %s crashed", job_id)
                s.rollback()
                self._fail(
                    s,
                    job,
                    "internal_error",
                    f"{type(exc).__name__}: {exc}",
                    {"traceback": traceback.format_exc()[-4000:]},
                )
            else:
                analysis.status = AnalysisStatus.COMPLETED.value
                analysis.stage = "completed"
                analysis.progress = 1.0
                analysis.results = results
                analysis.finished_at = utcnow()
                job.status = JobStatus.COMPLETED.value
                job.finished_at = utcnow()
            s.commit()

    @staticmethod
    def _fail(
        s: Session, job: ProcessingJob, code: str, message: str, details: dict[str, Any]
    ) -> None:
        job.status = JobStatus.FAILED.value
        job.finished_at = utcnow()
        job.error = f"{code}: {message}"
        analysis = s.get(Analysis, job.analysis_id)
        if analysis is not None:
            analysis.status = AnalysisStatus.FAILED.value
            analysis.stage = "failed"
            analysis.error_code = code
            analysis.error_message = message
            analysis.error_details = details
            analysis.finished_at = utcnow()

    def run_once(self) -> bool:
        job_id = self.claim_next()
        if job_id is None:
            return False
        self.run_job(job_id)
        return True

    def run_forever(self, poll_interval_s: float = 1.0) -> None:
        log.info("Worker %s polling every %.1fs", self.worker_id, poll_interval_s)
        while True:
            if not self.run_once():
                time.sleep(poll_interval_s)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tennis Performance Analyzer worker")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    parser.add_argument("--drain", action="store_true", help="Process queued jobs, then exit")
    parser.add_argument("--poll", type=float, default=1.0, help="Poll interval in seconds")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    os.environ.setdefault("GLOG_minloglevel", "2")  # quieten MediaPipe native logging

    worker = Worker(get_settings())
    worker.db.create_all()
    recovered = worker.recover_interrupted()
    if recovered:
        log.warning("Marked %d interrupted job(s) as failed", recovered)
    if args.once:
        worker.run_once()
    elif args.drain:
        while worker.run_once():
            pass
    else:
        try:
            worker.run_forever(args.poll)
        except KeyboardInterrupt:
            log.info("Worker stopped")


if __name__ == "__main__":
    main()

import time
import signal
import threading
import logging
from typing import Optional
import psycopg
from app.config import settings
from app.database import to_psycopg_conninfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("worker")


class HeartbeatManager:
    """
    Manages active lease renewals in a background thread to prevent lease expiry
    during long-running OCR or financial reconciliation jobs.
    Retries transient failures up to max_consecutive_failures before declaring lease lost.
    """
    def __init__(
        self,
        conn_str: str,
        task_id: str,
        lease_token: str,
        interval_sec: float = 90.0,
        max_consecutive_failures: int = 3,
    ):
        self.conn_str = to_psycopg_conninfo(conn_str)
        self.task_id = task_id
        self.lease_token = lease_token
        self.interval_sec = interval_sec
        self.max_consecutive_failures = max(1, max_consecutive_failures)
        self.stop_event = threading.Event()
        self.lease_lost = False
        self.thread: Optional[threading.Thread] = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        logger.debug("Heartbeat started for task %s", self.task_id)
        failures = 0
        while not self.stop_event.wait(self.interval_sec):
            try:
                with psycopg.connect(self.conn_str, autocommit=True, connect_timeout=10) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT public.renew_task_lease(%s, %s, %s);",
                            (self.task_id, self.lease_token, 5),
                        )
                        row = cur.fetchone()
                        new_expiry = row[0] if row else None
                        failures = 0
                        logger.info("Lease renewed for task %s, expires at %s", self.task_id, new_expiry)
            except Exception as e:
                failures += 1
                logger.warning(
                    "Heartbeat renewal attempt %d/%d failed for task %s: %s",
                    failures,
                    self.max_consecutive_failures,
                    self.task_id,
                    e,
                )
                if failures >= self.max_consecutive_failures:
                    logger.error("Lease marked lost for task %s after %d consecutive failures.", self.task_id, failures)
                    self.lease_lost = True
                    break

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        logger.debug("Heartbeat stopped for task %s", self.task_id)


class WorkerRunner:
    def __init__(self, org_id: Optional[str] = None, instance_label: str = "worker-default"):
        self.org_id = org_id or getattr(settings, "WORKER_ORG_ID", None)
        self.instance_label = instance_label
        self.conn_str = to_psycopg_conninfo(settings.DATABASE_WORKER_URL)
        self.running = True
        self._shutdown_event = threading.Event()
        self.current_task_id: Optional[str] = None
        self.current_lease_token: Optional[str] = None

        # Signal handlers may only set async-safe termination flags (no blocking DB/logging calls)
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._handle_shutdown)
            signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        self.running = False
        self._shutdown_event.set()

    def stop(self):
        self.running = False
        self._shutdown_event.set()

    def _release_current_task_safely(self):
        if self.current_task_id and self.current_lease_token:
            try:
                with psycopg.connect(self.conn_str, autocommit=True, connect_timeout=10) as conn:
                    self.release(conn, self.current_task_id, self.current_lease_token)
                    logger.info("Released task %s back to queue before shutdown.", self.current_task_id)
            except Exception as e:
                logger.warning("Could not release task during shutdown: %s", e)
            finally:
                self.current_task_id = None
                self.current_lease_token = None

    def run_once(self, conn) -> bool:
        """Executes a single poll-and-process cycle. Returns True if a task was claimed."""
        if not self.org_id:
            raise RuntimeError("WORKER_CONFIGURATION_ERROR: org_id is required to claim tenant tasks safely.")

        task_id, lease_token = self.claim(conn)
        if not (task_id and lease_token):
            return False

        self.current_task_id = task_id
        self.current_lease_token = lease_token
        logger.info("Claimed Task %s with secure lease token", task_id)

        heartbeat = HeartbeatManager(self.conn_str, task_id, lease_token, interval_sec=90.0)
        heartbeat.start()

        try:
            result = self.process_task(task_id, heartbeat)
            if heartbeat.lease_lost:
                raise TimeoutError(f"Task {task_id} processing aborted because lease renewal failed.")

            heartbeat.stop()
            self.complete(conn, task_id, lease_token, result)
            logger.info("Task %s successfully completed.", task_id)
        except Exception as e:
            heartbeat.stop()
            logger.error("Task %s processing failed: %s", task_id, e)
            try:
                self.fail(conn, task_id, lease_token, str(e))
                logger.info("Task %s transitioned to failure state.", task_id)
            except Exception as fail_err:
                logger.error("Failed to record failure status for task %s (lease likely expired): %s", task_id, fail_err)
        finally:
            self.current_task_id = None
            self.current_lease_token = None
        return True

    def run_loop(self, poll_interval_sec: float = 2.0, max_iterations: Optional[int] = None):
        logger.info("Starting hardened MyCA worker for Org: %s, Instance: %s", self.org_id, self.instance_label)
        backoff_sec = 2.0
        iterations = 0

        try:
            while self.running and (max_iterations is None or iterations < max_iterations):
                try:
                    with psycopg.connect(self.conn_str, autocommit=True, connect_timeout=10) as conn:
                        backoff_sec = 2.0
                        while self.running and (max_iterations is None or iterations < max_iterations):
                            iterations += 1
                            claimed = self.run_once(conn)
                            if not claimed and self.running:
                                self._shutdown_event.wait(poll_interval_sec)
                except Exception as conn_err:
                    logger.error("Worker DB connection error (reconnecting in %.1fs): %s", backoff_sec, conn_err)
                    if not self.running:
                        break
                    self._shutdown_event.wait(backoff_sec)
                    backoff_sec = min(backoff_sec * 2.0, 30.0)
        finally:
            self._release_current_task_safely()
            logger.info("Worker runner stopped cleanly.")

    def claim(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT o_task_id, o_lease_token FROM public.claim_task(%s, %s);",
                (self.org_id, self.instance_label),
            )
            row = cur.fetchone()
            if row and row[0]:
                return str(row[0]), str(row[1])
        return None, None

    def complete(self, conn, task_id: str, lease_token: str, result: dict):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.complete_task(%s, %s, %s::jsonb);",
                (task_id, lease_token, psycopg.types.json.Jsonb(result)),
            )

    def renew(self, conn, task_id: str, lease_token: str, minutes: int = 5):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.renew_task_lease(%s, %s, %s);",
                (task_id, lease_token, minutes),
            )

    def fail(self, conn, task_id: str, lease_token: str, error_message: str):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.fail_task(%s, %s, %s);",
                (task_id, lease_token, error_message),
            )

    def release(self, conn, task_id: str, lease_token: str):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.release_task(%s, %s);",
                (task_id, lease_token),
            )

    def process_task(self, task_id: str, heartbeat: HeartbeatManager) -> dict:
        """
        Routes claimed task to the appropriate processing pipeline inside a proper transaction.
        Rolls back partial DB changes on failure before task retry/fail state transition.
        """
        from app.modules.invoice_brain.service import process_invoice_ocr_task

        with psycopg.connect(self.conn_str, autocommit=False, connect_timeout=10) as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.current_org', %s, true);", (self.org_id,))
                    cur.execute("SELECT task_type, payload FROM public.worker_tasks WHERE id = %s;", (task_id,))
                    row = cur.fetchone()
                    if not row:
                        raise ValueError(f"Task {task_id} payload not found")

                    task_type, payload = row[0], row[1]
                    logger.info("Processing task %s of type '%s'", task_id, task_type)

                    if heartbeat.lease_lost:
                        raise TimeoutError(f"Task {task_id} lease lost before execution.")

                    if task_type == "invoice_ocr":
                        res = process_invoice_ocr_task(
                            db_conn=conn,
                            task_id=task_id,
                            invoice_id=payload.get("invoice_id"),
                            document_id=payload.get("document_id"),
                            org_id=self.org_id,
                            file_path=payload.get("file_path"),
                            mime_type=payload.get("mime_type", "application/pdf"),
                            lease_token=heartbeat.lease_token,
                        )
                    else:
                        res = {"status": "success", "task_type": task_type, "processed_task": task_id}

                conn.commit()
                return res
            except Exception:
                conn.rollback()
                raise

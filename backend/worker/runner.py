import time
import signal
import threading
import logging
import psycopg
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("worker")

class HeartbeatManager:
    """
    Manages active lease renewals in a background thread to prevent lease expiry
    during long-running OCR or financial reconciliation jobs.
    """
    def __init__(self, conn_str: str, task_id: str, lease_token: str, interval_sec: float = 90.0):
        self.conn_str = conn_str
        self.task_id = task_id
        self.lease_token = lease_token
        self.interval_sec = interval_sec
        self.stop_event = threading.Event()
        self.lease_lost = False
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        logger.debug(f"Heartbeat started for task {self.task_id}")
        while not self.stop_event.wait(self.interval_sec):
            try:
                with psycopg.connect(self.conn_str, autocommit=True) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT public.renew_task_lease(%s, %s, %s);",
                            (self.task_id, self.lease_token, 5)
                        )
                        new_expiry = cur.fetchone()[0]
                        logger.info(f"Lease renewed for task {self.task_id}, expires at {new_expiry}")
            except Exception as e:
                logger.error(f"Heartbeat failed to renew lease for task {self.task_id}: {e}")
                self.lease_lost = True
                break

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        logger.debug(f"Heartbeat stopped for task {self.task_id}")


class WorkerRunner:
    def __init__(self, org_id: str, instance_label: str = "worker-default"):
        self.org_id = org_id
        self.instance_label = instance_label
        self.conn_str = settings.DATABASE_WORKER_URL
        self.running = True
        self.current_task_id = None
        self.current_lease_token = None

        # Setup graceful shutdown handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        logger.info(f"Received signal {signum}. Initiating graceful shutdown...")
        self.running = False
        if self.current_task_id and self.current_lease_token:
            try:
                with psycopg.connect(self.conn_str, autocommit=True) as conn:
                    self.release(conn, self.current_task_id, self.current_lease_token)
                    logger.info(f"Released task {self.current_task_id} back to queue before shutdown.")
            except Exception as e:
                logger.warning(f"Could not release task during shutdown: {e}")

    def run_loop(self, poll_interval_sec: float = 2.0):
        logger.info(f"Starting hardened MyCA worker for Org: {self.org_id}, Instance: {self.instance_label}")
        with psycopg.connect(self.conn_str, autocommit=True) as conn:
            while self.running:
                task_id, lease_token = self.claim(conn)
                if task_id and lease_token:
                    self.current_task_id = task_id
                    self.current_lease_token = lease_token
                    logger.info(f"Claimed Task {task_id} with secure lease token")

                    # Start active heartbeat renewal
                    heartbeat = HeartbeatManager(self.conn_str, task_id, lease_token, interval_sec=90.0)
                    heartbeat.start()

                    try:
                        result = self.process_task(task_id, heartbeat)
                        if heartbeat.lease_lost:
                            raise TimeoutError(f"Task {task_id} processing aborted because lease renewal failed.")

                        heartbeat.stop()
                        self.complete(conn, task_id, lease_token, result)
                        logger.info(f"Task {task_id} successfully completed.")
                    except Exception as e:
                        heartbeat.stop()
                        logger.error(f"Task {task_id} processing failed: {e}")
                        try:
                            self.fail(conn, task_id, lease_token, str(e))
                            logger.info(f"Task {task_id} transitioned to failure state.")
                        except Exception as fail_err:
                            logger.error(f"Failed to record failure status for task {task_id} (lease likely expired): {fail_err}")
                    finally:
                        self.current_task_id = None
                        self.current_lease_token = None
                else:
                    time.sleep(poll_interval_sec)

        logger.info("Worker runner stopped cleanly.")

    def claim(self, conn):
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT o_task_id, o_lease_token FROM public.claim_task(%s, %s);",
                    (self.org_id, self.instance_label)
                )
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0]), str(row[1])
        except Exception as e:
            logger.error(f"Error querying claim_task: {e}")
        return None, None

    def complete(self, conn, task_id: str, lease_token: str, result: dict):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.complete_task(%s, %s, %s::jsonb);",
                (task_id, lease_token, psycopg.types.json.Jsonb(result))
            )

    def renew(self, conn, task_id: str, lease_token: str, minutes: int = 5):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.renew_task_lease(%s, %s, %s);",
                (task_id, lease_token, minutes)
            )

    def fail(self, conn, task_id: str, lease_token: str, error_message: str):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.fail_task(%s, %s, %s);",
                (task_id, lease_token, error_message)
            )

    def release(self, conn, task_id: str, lease_token: str):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.release_task(%s, %s);",
                (task_id, lease_token)
            )

    def process_task(self, task_id: str, heartbeat: HeartbeatManager) -> dict:
        """
        Routes claimed task to the appropriate processing pipeline.
        Invoice Brain: Executes OCR extraction and deterministic GST validation.
        """
        from app.modules.invoice_brain.service import process_invoice_ocr_task

        with psycopg.connect(self.conn_str, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.current_org', %s, false);", (self.org_id,))
                cur.execute("SELECT task_type, payload FROM public.worker_tasks WHERE id = %s;", (task_id,))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Task {task_id} payload not found")
                
                task_type, payload = row[0], row[1]
                logger.info(f"Processing task {task_id} of type '{task_type}'")

                if task_type == "invoice_ocr":
                    invoice_id = payload.get("invoice_id")
                    document_id = payload.get("document_id")
                    file_path = payload.get("file_path")
                    mime_type = payload.get("mime_type", "application/pdf")
                    
                    return process_invoice_ocr_task(
                        db_conn=conn,
                        task_id=task_id,
                        invoice_id=invoice_id,
                        document_id=document_id,
                        org_id=self.org_id,
                        file_path=file_path,
                        mime_type=mime_type,
                        lease_token=heartbeat.lease_token
                    )
                else:
                    return {"status": "success", "task_type": task_type, "processed_task": task_id}

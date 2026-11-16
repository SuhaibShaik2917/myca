import time
import logging
import psycopg
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("worker")

class WorkerRunner:
    def __init__(self, org_id: str, instance_label: str = "worker-default"):
        self.org_id = org_id
        self.instance_label = instance_label
        self.conn_str = settings.DATABASE_WORKER_URL

    def run_loop(self, poll_interval_sec: float = 2.0):
        logger.info(f"Starting MyCA worker for Org: {self.org_id}, Instance: {self.instance_label}")
        with psycopg.connect(self.conn_str, autocommit=True) as conn:
            while True:
                task_id, lease_token = self.claim(conn)
                if task_id and lease_token:
                    logger.info(f"Claimed Task {task_id} with secure lease token")
                    try:
                        result = self.process_task(task_id)
                        self.complete(conn, task_id, lease_token, result)
                        logger.info(f"Task {task_id} successfully completed.")
                    except Exception as e:
                        logger.error(f"Task {task_id} failed: {e}")
                        self.fail(conn, task_id, lease_token, str(e))
                else:
                    time.sleep(poll_interval_sec)

    def claim(self, conn):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT o_task_id, o_lease_token FROM public.claim_task(%s, %s);",
                (self.org_id, self.instance_label)
            )
            row = cur.fetchone()
            if row and row[0]:
                return str(row[0]), str(row[1])
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

    def process_task(self, task_id: str) -> dict:
        # Business processing (OCR, reconciliation, scoring)
        return {"status": "success", "processed_task": task_id}

"""A small reliable job queue on Redis.

- enqueue:  LPUSH onto the ready list.
- reserve:  BLMOVE ready -> this worker's processing list, atomically, so a job
            is never lost if the worker crashes mid-job (at-least-once delivery).
- ack:      LREM from the processing list once the job's outcome is persisted.
- retry:    ZADD into a delayed set scored by due time; promote_due() moves due
            jobs back to ready. Gives exponential backoff without sleeping.
- recover:  on startup, push anything left in this worker's processing list
            back to ready (the previous process died holding it).

Because delivery is at-least-once, job handlers must be idempotent.
"""

import time

import redis


class JobQueue:
    def __init__(self, client: redis.Redis, name: str, worker_id: str = "worker-1"):
        self.r = client
        self.ready = f"q:{name}:ready"
        self.processing = f"q:{name}:processing:{worker_id}"
        self.delayed = f"q:{name}:delayed"

    def enqueue(self, job_id: int | str) -> None:
        self.r.lpush(self.ready, str(job_id))

    def reserve(self, timeout_s: float = 1.0) -> str | None:
        job = self.r.blmove(self.ready, self.processing, timeout_s, "RIGHT", "LEFT")
        return job.decode() if isinstance(job, bytes) else job

    def ack(self, job_id: int | str) -> None:
        self.r.lrem(self.processing, 1, str(job_id))

    def retry_later(self, job_id: int | str, delay_s: float) -> None:
        pipe = self.r.pipeline()
        pipe.zadd(self.delayed, {str(job_id): time.time() + delay_s})
        pipe.lrem(self.processing, 1, str(job_id))
        pipe.execute()

    def promote_due(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        due = self.r.zrangebyscore(self.delayed, 0, now)
        moved = 0
        for job in due:
            if self.r.zrem(self.delayed, job):  # only the worker that removes it re-enqueues it
                self.r.lpush(self.ready, job)
                moved += 1
        return moved

    def recover(self) -> int:
        moved = 0
        while self.r.lmove(self.processing, self.ready, "RIGHT", "LEFT"):
            moved += 1
        return moved

    def depth(self) -> dict[str, int]:
        return {
            "ready": self.r.llen(self.ready),
            "processing": self.r.llen(self.processing),
            "delayed": self.r.zcard(self.delayed),
        }

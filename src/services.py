import asyncio
import time
import csv
import io
from typing import Optional, Callable, Awaitable
from src.database import DatabaseRepository
from src.models import ExecutionLogDTO


class AsyncWorkerPool:
    def __init__(
        self,
        repo: DatabaseRepository,
        workers_count: int = 3,
        probe_func: Optional[Callable[[str], Awaitable[tuple[bool, str]]]] = None,
    ):
        self.repo = repo
        self.workers_count = workers_count
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running = False
        self.probe_func = probe_func or self._default_endpoint_probe

    async def _default_endpoint_probe(self, endpoint: str) -> tuple[bool, str]:
        await asyncio.sleep(0.05)
        if not endpoint or "fail" in endpoint.lower() or "error" in endpoint.lower():
            return False, "Endpoint unreachable or returned status 500"
        return True, f"OK (200) verified endpoint: {endpoint}"

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for i in range(self.workers_count):
            worker_task = asyncio.create_task(self._worker_loop(i))
            self._workers.append(worker_task)

    async def stop(self) -> None:
        self._running = False
        await self.queue.join()
        for w in self._workers:
            w.cancel()
        self._workers.clear()

    async def enqueue_task(self, task_id: int) -> None:
        await self.queue.put(task_id)

    async def execute_task_immediately(self, task_id: int) -> ExecutionLogDTO:
        task = await self.repo.get_task_by_id(task_id)
        if not task:
            raise ValueError(f"Задача #{task_id} не найдена в БД")
        await self.repo.update_task_status(task_id, "running")
        start_ts = time.perf_counter()
        ok, details = await self.probe_func(task.target_endpoint)
        elapsed_ms = round((time.perf_counter() - start_ts) * 1000.0, 2)

        final_status = "completed" if ok else "failed"
        await self.repo.update_task_status(task_id, final_status)
        log_dto = ExecutionLogDTO(
            id=None,
            task_id=task_id,
            result_status=final_status,
            latency_ms=elapsed_ms,
            details=details,
        )
        log_id = await self.repo.add_execution_log(log_dto)
        log_dto.id = log_id
        return log_dto

    async def _worker_loop(self, worker_idx: int) -> None:
        while self._running:
            try:
                task_id = await self.queue.get()
                try:
                    await self.execute_task_immediately(task_id)
                finally:
                    self.queue.task_done()
            except asyncio.CancelledError:
                break


class ReportGeneratorService:
    def __init__(self, repo: DatabaseRepository):
        self.repo = repo

    async def export_user_tasks_csv(self, user_id: int) -> str:
        tasks = await self.repo.get_user_tasks(user_id)
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(
            ["ID", "Title", "Category", "Endpoint", "Priority", "Status", "Updated_At"]
        )
        for t in tasks:
            writer.writerow(
                [
                    t.id,
                    t.title,
                    t.category,
                    t.target_endpoint,
                    t.priority,
                    t.status,
                    t.updated_at,
                ]
            )
        return output.getvalue()

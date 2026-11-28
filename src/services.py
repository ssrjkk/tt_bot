import asyncio
import csv
import io
import logging
import time
from contextlib import suppress
from typing import Any, Awaitable, Callable, Optional, Union

from src.database import DatabaseRepository
from src.models import ExecutionLogDTO, ProbeResult, TaskDTO
from src.probes import EndpointProber

logger = logging.getLogger(__name__)

ProbeCallable = Callable[[str], Awaitable[Union[ProbeResult, tuple[bool, str]]]]
AlertCallback = Callable[[TaskDTO, ExecutionLogDTO, bool], Awaitable[None]]

CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _sanitize_csv_cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(CSV_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def _normalize_probe_result(raw: Union[ProbeResult, tuple[bool, str]]) -> ProbeResult:
    if isinstance(raw, ProbeResult):
        return raw
    ok, details = raw
    return ProbeResult(ok=bool(ok), details=str(details))


class AsyncWorkerPool:
    def __init__(
        self,
        repo: DatabaseRepository,
        workers_count: int = 3,
        probe_func: Optional[ProbeCallable] = None,
        prober: Optional[EndpointProber] = None,
        alert_callback: Optional[AlertCallback] = None,
    ) -> None:
        self.repo = repo
        self.workers_count = max(1, workers_count)
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._in_flight: set[int] = set()
        self._running = False
        self._prober = prober or EndpointProber()
        self.probe_func = probe_func
        self.alert_callback = alert_callback

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for idx in range(self.workers_count):
            self._workers.append(asyncio.create_task(self._worker_loop(idx)))

    async def stop(self, drain_timeout: float = 10.0) -> None:
        self._running = False
        try:
            await asyncio.wait_for(self.queue.join(), timeout=drain_timeout)
        except asyncio.TimeoutError:
            logger.warning("Worker pool drain timed out; cancelling active workers")
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._in_flight.clear()

    async def enqueue_task(self, task_id: int) -> bool:
        if task_id in self._in_flight:
            return False
        self._in_flight.add(task_id)
        await self.queue.put(task_id)
        return True

    async def run_all_user_tasks(self, user_id: int) -> int:
        tasks = await self.repo.get_user_tasks(user_id)
        queued = 0
        for task in tasks:
            if task.enabled and task.id is not None:
                if await self.enqueue_task(task.id):
                    queued += 1
        return queued

    async def run_failed_user_tasks(self, user_id: int) -> int:
        tasks = await self.repo.get_user_tasks(user_id, status_filter="failed")
        queued = 0
        for task in tasks:
            if task.enabled and task.id is not None:
                if await self.enqueue_task(task.id):
                    queued += 1
        return queued

    async def _run_probe(self, task: TaskDTO) -> ProbeResult:
        try:
            if self.probe_func is not None:
                raw = await self.probe_func(task.target_endpoint)
                return _normalize_probe_result(raw)
            return await self._prober.probe(
                task.target_endpoint,
                expected_status=task.expected_status,
                expected_text=task.expected_text,
                ssl_min_days=task.ssl_min_days,
            )
        except Exception as exc:
            logger.exception("Probe crashed for task #%s", task.id)
            return ProbeResult(ok=False, details=f"Внутренняя ошибка проверки: {exc}")

    async def execute_task_immediately(
        self, task_id: int, user_id: Optional[int] = None
    ) -> Optional[ExecutionLogDTO]:
        task = await self.repo.get_task_by_id(task_id, user_id=user_id)
        if not task:
            raise ValueError(f"Задача #{task_id} не найдена в БД")

        prev_failures = task.consecutive_failures
        await self.repo.update_task_status(task_id, "running")
        start_ts = time.perf_counter()
        result = await self._run_probe(task)
        elapsed_ms = round((time.perf_counter() - start_ts) * 1000.0, 2)

        final_status = "completed" if result.ok else "failed"
        new_failures = 0 if result.ok else prev_failures + 1

        root = await self.repo.find_root_cause(task_id)
        alert_event = self._alert_event(task, prev_failures, new_failures)
        try:
            log_dto = await self.repo.record_check_result(
                task_id=task_id,
                result_status=final_status,
                latency_ms=elapsed_ms,
                details=result.details,
                status_code=result.status_code,
                consecutive_failures=new_failures,
                ssl_days_left=result.ssl_days_left,
                suppressed=bool(alert_event is not None and root),
                root_task_id=root.id if root else None,
            )
        except Exception:
            logger.exception("Не удалось сохранить результат проверки #%s", task_id)
            with suppress(Exception):
                await self.repo.update_task_status(task_id, task.status)
            return None

        if log_dto is None:
            logger.info("Монитор #%s удалён во время проверки", task_id)
            return None

        await self._maybe_notify(task, log_dto, prev_failures, new_failures)
        return log_dto

    @staticmethod
    def _alert_event(
        task: TaskDTO, prev_failures: int, new_failures: int
    ) -> Optional[bool]:
        """None — порога не касались; False — упал; True — восстановился."""
        threshold = max(1, task.alert_threshold)
        if new_failures == threshold:
            return False
        if new_failures == 0 and prev_failures >= threshold:
            return True
        return None

    async def _maybe_notify(
        self,
        task: TaskDTO,
        log_dto: ExecutionLogDTO,
        prev_failures: int,
        new_failures: int,
    ) -> None:
        if not self.alert_callback or log_dto.suppressed:
            return
        event = self._alert_event(task, prev_failures, new_failures)
        if event is None:
            return
        try:
            await self.alert_callback(task, log_dto, event)
        except Exception:
            logger.exception("Failed to send alert for task #%s", task.id)

    async def _worker_loop(self, worker_idx: int) -> None:
        while self._running or not self.queue.empty():
            try:
                task_id = await asyncio.wait_for(self.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                await self.execute_task_immediately(task_id)
            except Exception:
                logger.exception(
                    "Worker #%s failed processing task #%s", worker_idx, task_id
                )
            finally:
                self._in_flight.discard(task_id)
                self.queue.task_done()


class MonitorScheduler:
    """Фоновый планировщик периодического мониторинга и очистки логов."""

    def __init__(
        self,
        repo: DatabaseRepository,
        worker_pool: AsyncWorkerPool,
        tick_sec: float = 15.0,
        retention_days: int = 30,
    ) -> None:
        self.repo = repo
        self.worker_pool = worker_pool
        self.tick_sec = tick_sec
        self.retention_days = retention_days
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._last_prune_ts = 0.0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def run_once(self) -> int:
        due_tasks = await self.repo.get_due_tasks()
        queued = 0
        for task in due_tasks:
            if task.id is not None and await self.worker_pool.enqueue_task(task.id):
                queued += 1
        now_mono = time.monotonic()
        if now_mono - self._last_prune_ts >= 3600:
            await self.repo.prune_old_logs(self.retention_days)
            self._last_prune_ts = now_mono
        return queued

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Scheduler tick failed")
            try:
                await asyncio.sleep(self.tick_sec)
            except asyncio.CancelledError:
                break


class ReportGeneratorService:
    def __init__(self, repo: DatabaseRepository) -> None:
        self.repo = repo

    async def export_user_tasks_csv(self, user_id: int) -> str:
        tasks = await self.repo.get_user_tasks(user_id)
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(
            [
                "ID",
                "Title",
                "Category",
                "Endpoint",
                "Interval_Sec",
                "Priority",
                "Status",
                "Enabled",
                "Last_Checked_At",
                "Updated_At",
            ]
        )
        for task in tasks:
            writer.writerow(
                [
                    _sanitize_csv_cell(v)
                    for v in (
                        task.id,
                        task.title,
                        task.category,
                        task.target_endpoint,
                        task.check_interval_sec,
                        task.priority,
                        task.status,
                        task.enabled,
                        task.last_checked_at or "",
                        task.updated_at or "",
                    )
                ]
            )
        return output.getvalue()

    async def export_task_logs_csv(
        self, user_id: int, task_id: int, limit: int = 200
    ) -> Optional[str]:
        task = await self.repo.get_task_by_id(task_id, user_id=user_id)
        if not task:
            return None
        logs = await self.repo.get_task_logs(task_id, limit=limit, user_id=user_id)
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(
            [
                "Log_ID",
                "Task_ID",
                "Status",
                "HTTP_Code",
                "SSL_Days",
                "Latency_ms",
                "Details",
                "At",
            ]
        )
        for entry in logs:
            writer.writerow(
                [
                    _sanitize_csv_cell(v)
                    for v in (
                        entry.id,
                        entry.task_id,
                        entry.result_status,
                        entry.status_code if entry.status_code is not None else "",
                        entry.ssl_days_left if entry.ssl_days_left is not None else "",
                        entry.latency_ms,
                        entry.details,
                        entry.executed_at or "",
                    )
                ]
            )
        return output.getvalue()

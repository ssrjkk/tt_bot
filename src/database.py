import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

from src.models import ExecutionLogDTO, TaskDTO, UserDTO

TASK_MIGRATIONS = (
    ("check_interval_sec", "INTEGER NOT NULL DEFAULT 0"),
    ("expected_status", "INTEGER NOT NULL DEFAULT 200"),
    ("expected_text", "TEXT NOT NULL DEFAULT ''"),
    ("ssl_min_days", "INTEGER NOT NULL DEFAULT 14"),
    ("enabled", "INTEGER NOT NULL DEFAULT 1"),
    ("consecutive_failures", "INTEGER NOT NULL DEFAULT 0"),
    ("alert_threshold", "INTEGER NOT NULL DEFAULT 2"),
    ("parent_id", "INTEGER"),
    ("last_checked_at", "TEXT"),
)

LOG_MIGRATIONS = (
    ("status_code", "INTEGER"),
    ("ssl_days_left", "INTEGER"),
    ("suppressed", "INTEGER NOT NULL DEFAULT 0"),
    ("root_task_id", "INTEGER"),
)

# Глубина цепи `parent_id`, которую движок ещё считает деревом, а не циклом.
MAX_TREE_DEPTH = 12


def _row_to_task(row: aiosqlite.Row) -> TaskDTO:
    data = dict(row)
    data["enabled"] = bool(data.get("enabled", 1))
    return TaskDTO(**data)


def _row_to_log(row: aiosqlite.Row) -> ExecutionLogDTO:
    data = dict(row)
    data["suppressed"] = bool(data.get("suppressed", 0))
    return ExecutionLogDTO(**data)


def _compute_p95(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))
    return round(ordered[idx], 2)


class DatabaseRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def init_db(self) -> None:
        if self.db_path != ":memory:":
            parent = os.path.dirname(self.db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._apply_pragmas()
        await self._create_tables()
        await self._migrate_schema()
        await self._create_indexes()
        await self._db.commit()

    async def _apply_pragmas(self) -> None:
        pragmas = (
            "PRAGMA journal_mode=WAL;",
            "PRAGMA synchronous=NORMAL;",
            "PRAGMA foreign_keys=ON;",
            "PRAGMA busy_timeout=5000;",
            "PRAGMA cache_size=-64000;",
            "PRAGMA temp_store=MEMORY;",
        )
        for stmt in pragmas:
            await self._db.execute(stmt)

    async def _create_tables(self) -> None:
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL
            );
            """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                target_endpoint TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL DEFAULT 2 CHECK(priority IN (1, 2, 3)),
                status TEXT NOT NULL DEFAULT 'pending',
                check_interval_sec INTEGER NOT NULL DEFAULT 0,
                expected_status INTEGER NOT NULL DEFAULT 200,
                expected_text TEXT NOT NULL DEFAULT '',
                ssl_min_days INTEGER NOT NULL DEFAULT 14,
                enabled INTEGER NOT NULL DEFAULT 1,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                alert_threshold INTEGER NOT NULL DEFAULT 2,
                parent_id INTEGER,
                last_checked_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS execution_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                result_status TEXT NOT NULL,
                latency_ms REAL NOT NULL,
                details TEXT NOT NULL,
                status_code INTEGER,
                ssl_days_left INTEGER,
                suppressed INTEGER NOT NULL DEFAULT 0,
                root_task_id INTEGER,
                executed_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );
            """)
        # parent_id сознательно без FOREIGN KEY: ALTER TABLE не умеет добавлять
        # ограничения, а свежая и мигрированная база должны вести себя одинаково.
        # Целостность цепи держат set_task_parent() и delete_task().

    async def _migrate_schema(self) -> None:
        await self._add_missing_columns("tasks", TASK_MIGRATIONS)
        await self._add_missing_columns("execution_logs", LOG_MIGRATIONS)

    async def _add_missing_columns(
        self, table: str, migrations: tuple[tuple[str, str], ...]
    ) -> None:
        cursor = await self._db.execute(f"PRAGMA table_info({table});")
        existing = {row["name"] for row in await cursor.fetchall()}
        for col_name, col_def in migrations:
            if col_name not in existing:
                await self._db.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def};"
                )

    async def _create_indexes(self) -> None:
        indexes = (
            "CREATE INDEX IF NOT EXISTS idx_tasks_user_status "
            "ON tasks(user_id, status);",
            "CREATE INDEX IF NOT EXISTS idx_tasks_schedule "
            "ON tasks(enabled, check_interval_sec);",
            "CREATE INDEX IF NOT EXISTS idx_logs_task_exec "
            "ON execution_logs(task_id, executed_at);",
            "CREATE INDEX IF NOT EXISTS idx_logs_status_exec "
            "ON execution_logs(result_status, executed_at);",
        )
        for stmt in indexes:
            await self._db.execute(stmt)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def reset_stuck_running_tasks(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "UPDATE tasks SET status = 'pending', updated_at = ? "
            "WHERE status = 'running'",
            (now,),
        )
        await self._db.commit()
        return cursor.rowcount

    async def upsert_user(self, user: UserDTO) -> UserDTO:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT INTO users (id, username, full_name, role, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name;
            """,
            (user.id, user.username, user.full_name, user.role, now),
        )
        await self._db.commit()
        user.created_at = now
        return user

    async def create_task(self, task: TaskDTO) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            """
            INSERT INTO tasks (
                user_id, title, category, target_endpoint, priority, status,
                check_interval_sec, expected_status, expected_text, ssl_min_days,
                enabled, consecutive_failures, alert_threshold, parent_id,
                last_checked_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.user_id,
                task.title,
                task.category,
                task.target_endpoint,
                task.priority,
                task.status,
                task.check_interval_sec,
                task.expected_status,
                task.expected_text,
                task.ssl_min_days,
                int(task.enabled),
                task.consecutive_failures,
                task.alert_threshold,
                task.parent_id,
                task.last_checked_at,
                now,
                now,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_task_by_id(
        self, task_id: int, user_id: Optional[int] = None
    ) -> Optional[TaskDTO]:
        if user_id is not None:
            cursor = await self._db.execute(
                "SELECT * FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, user_id),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            )
        row = await cursor.fetchone()
        return _row_to_task(row) if row else None

    async def get_user_tasks(
        self,
        user_id: int,
        status_filter: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[TaskDTO]:
        params: List[Any] = [user_id]
        query = "SELECT * FROM tasks WHERE user_id = ?"
        if status_filter and status_filter != "all":
            query += " AND status = ?"
            params.append(status_filter)
        query += " ORDER BY priority DESC, id DESC"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_task(r) for r in rows]

    async def count_user_tasks(
        self, user_id: int, status_filter: Optional[str] = None
    ) -> int:
        if status_filter and status_filter != "all":
            cursor = await self._db.execute(
                "SELECT COUNT(*) AS cnt FROM tasks " "WHERE user_id = ? AND status = ?",
                (user_id, status_filter),
            )
        else:
            cursor = await self._db.execute(
                "SELECT COUNT(*) AS cnt FROM tasks WHERE user_id = ?",
                (user_id,),
            )
        row = await cursor.fetchone()
        return int(row["cnt"])

    async def update_task_status(self, task_id: int, new_status: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, task_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def update_task_priority(
        self, task_id: int, new_priority: int, user_id: Optional[int] = None
    ) -> bool:
        if new_priority not in (1, 2, 3):
            raise ValueError("Приоритет должен быть в диапазоне от 1 до 3")
        now = datetime.now(timezone.utc).isoformat()
        if user_id is not None:
            cursor = await self._db.execute(
                "UPDATE tasks SET priority = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (new_priority, now, task_id, user_id),
            )
        else:
            cursor = await self._db.execute(
                "UPDATE tasks SET priority = ?, updated_at = ? WHERE id = ?",
                (new_priority, now, task_id),
            )
        await self._db.commit()
        return cursor.rowcount > 0

    async def update_task_interval(
        self, task_id: int, interval_sec: int, user_id: Optional[int] = None
    ) -> bool:
        if interval_sec < 0:
            raise ValueError("Интервал проверки не может быть отрицательным")
        now = datetime.now(timezone.utc).isoformat()
        if user_id is not None:
            cursor = await self._db.execute(
                "UPDATE tasks SET check_interval_sec = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (interval_sec, now, task_id, user_id),
            )
        else:
            cursor = await self._db.execute(
                "UPDATE tasks SET check_interval_sec = ?, updated_at = ? "
                "WHERE id = ?",
                (interval_sec, now, task_id),
            )
        await self._db.commit()
        return cursor.rowcount > 0

    async def update_task_threshold(
        self, task_id: int, threshold: int, user_id: Optional[int] = None
    ) -> bool:
        if not (1 <= threshold <= 5):
            raise ValueError("Порог алертов должен быть от 1 до 5")
        now = datetime.now(timezone.utc).isoformat()
        if user_id is not None:
            cursor = await self._db.execute(
                "UPDATE tasks SET alert_threshold = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (threshold, now, task_id, user_id),
            )
        else:
            cursor = await self._db.execute(
                "UPDATE tasks SET alert_threshold = ?, updated_at = ? WHERE id = ?",
                (threshold, now, task_id),
            )
        await self._db.commit()
        return cursor.rowcount > 0

    async def toggle_task_enabled(self, task_id: int, user_id: int) -> Optional[bool]:
        task = await self.get_task_by_id(task_id, user_id=user_id)
        if not task:
            return None
        new_state = not task.enabled
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE tasks SET enabled = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (int(new_state), now, task_id, user_id),
        )
        await self._db.commit()
        return new_state

    async def set_all_tasks_enabled(self, user_id: int, enabled: bool) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "UPDATE tasks SET enabled = ?, updated_at = ? WHERE user_id = ?",
            (int(enabled), now, user_id),
        )
        await self._db.commit()
        return cursor.rowcount

    async def update_task_expected_text(
        self, task_id: int, expected_text: str, user_id: Optional[int] = None
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        clean = expected_text.strip()[:120]
        if user_id is not None:
            cursor = await self._db.execute(
                "UPDATE tasks SET expected_text = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (clean, now, task_id, user_id),
            )
        else:
            cursor = await self._db.execute(
                "UPDATE tasks SET expected_text = ?, updated_at = ? WHERE id = ?",
                (clean, now, task_id),
            )
        await self._db.commit()
        return cursor.rowcount > 0

    async def get_task_parent(self, task_id: int) -> Optional[TaskDTO]:
        cursor = await self._db.execute(
            """
            SELECT p.* FROM tasks p
            JOIN tasks c ON c.parent_id = p.id
            WHERE c.id = ?
            """,
            (task_id,),
        )
        row = await cursor.fetchone()
        return _row_to_task(row) if row else None

    async def _ancestor_chain(self, task_id: int) -> List[TaskDTO]:
        """Предки вверх по parent_id. Замкнутая вручную цепь не зацикливает обход."""
        chain: List[TaskDTO] = []
        seen = {task_id}
        current = task_id
        while len(chain) < MAX_TREE_DEPTH:
            parent = await self.get_task_parent(current)
            if parent is None or parent.id in seen:
                break
            seen.add(parent.id)
            chain.append(parent)
            current = parent.id
        return chain

    async def find_root_cause(self, task_id: int) -> Optional[TaskDTO]:
        """Верхний упавший предок: падение ребёнка — симптом, а не новый инцидент."""
        blocked_by: Optional[TaskDTO] = None
        for ancestor in await self._ancestor_chain(task_id):
            if ancestor.enabled and ancestor.status == "failed":
                blocked_by = ancestor
        return blocked_by

    async def set_task_parent(
        self, task_id: int, parent_id: Optional[int], user_id: int
    ) -> bool:
        if parent_id is not None:
            await self._validate_parent_link(task_id, parent_id, user_id)
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "UPDATE tasks SET parent_id = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (parent_id, now, task_id, user_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def _validate_parent_link(
        self, task_id: int, parent_id: int, user_id: int
    ) -> None:
        if parent_id == task_id:
            raise ValueError("Монитор не может быть сам себе родителем")
        parent = await self.get_task_by_id(parent_id, user_id=user_id)
        if parent is None:
            raise ValueError(
                "Родительский монитор не найден или принадлежит другому пользователю"
            )
        chain = await self._ancestor_chain(parent_id)
        if any(node.id == task_id for node in chain):
            raise ValueError("Такая связь замкнула бы цикл в дереве мониторов")
        if len(chain) + 1 > MAX_TREE_DEPTH:
            raise ValueError(f"Цепочка длиннее {MAX_TREE_DEPTH} мониторов")

    async def delete_task(self, task_id: int, user_id: Optional[int] = None) -> bool:
        if user_id is not None:
            cursor = await self._db.execute(
                "DELETE FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, user_id),
            )
        else:
            cursor = await self._db.execute(
                "DELETE FROM tasks WHERE id = ?", (task_id,)
            )
        if cursor.rowcount:
            # Ребёнок удалённого монитора становится корнем: висячий parent_id
            # спрятал бы ветку от /tree и нашёл бы «несуществующего родителя».
            await self._db.execute(
                "UPDATE tasks SET parent_id = NULL WHERE parent_id = ?", (task_id,)
            )
        await self._db.commit()
        return cursor.rowcount > 0

    async def record_check_result(
        self,
        task_id: int,
        result_status: str,
        latency_ms: float,
        details: str,
        status_code: Optional[int],
        consecutive_failures: int,
        ssl_days_left: Optional[int] = None,
        suppressed: bool = False,
        root_task_id: Optional[int] = None,
    ) -> Optional[ExecutionLogDTO]:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            """
            UPDATE tasks
            SET status = ?, consecutive_failures = ?,
                last_checked_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (result_status, consecutive_failures, now, now, task_id),
        )
        if cursor.rowcount == 0:
            await self._db.rollback()
            return None
        try:
            insert = await self._db.execute(
                """
                INSERT INTO execution_logs (
                    task_id, result_status, latency_ms, details,
                    status_code, ssl_days_left, suppressed, root_task_id, executed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    result_status,
                    latency_ms,
                    details,
                    status_code,
                    ssl_days_left,
                    int(suppressed),
                    root_task_id,
                    now,
                ),
            )
        except Exception:
            await self._db.rollback()
            raise
        await self._db.commit()
        return ExecutionLogDTO(
            id=insert.lastrowid,
            task_id=task_id,
            result_status=result_status,
            latency_ms=latency_ms,
            details=details,
            status_code=status_code,
            ssl_days_left=ssl_days_left,
            suppressed=bool(suppressed),
            root_task_id=root_task_id,
            executed_at=now,
        )

    async def add_execution_log(self, log_entry: ExecutionLogDTO) -> int:
        now = log_entry.executed_at or datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            """
            INSERT INTO execution_logs (
                task_id, result_status, latency_ms, details,
                status_code, ssl_days_left, suppressed, root_task_id, executed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_entry.task_id,
                log_entry.result_status,
                log_entry.latency_ms,
                log_entry.details,
                log_entry.status_code,
                log_entry.ssl_days_left,
                int(log_entry.suppressed),
                log_entry.root_task_id,
                now,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_task_logs(
        self, task_id: int, limit: int = 10, user_id: Optional[int] = None
    ) -> List[ExecutionLogDTO]:
        if user_id is not None:
            cursor = await self._db.execute(
                """
                SELECT l.* FROM execution_logs l
                JOIN tasks t ON l.task_id = t.id
                WHERE l.task_id = ? AND t.user_id = ?
                ORDER BY l.id DESC LIMIT ?
                """,
                (task_id, user_id, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM execution_logs WHERE task_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (task_id, limit),
            )
        rows = await cursor.fetchall()
        return [_row_to_log(r) for r in rows]

    async def get_recent_incidents(
        self, user_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        cursor = await self._db.execute(
            """
            SELECT l.id, l.task_id, t.title, t.target_endpoint,
                   l.latency_ms, l.details, l.executed_at,
                   l.suppressed, r.id AS root_id, r.title AS root_title
            FROM execution_logs l
            JOIN tasks t ON l.task_id = t.id
            LEFT JOIN tasks r ON r.id = l.root_task_id
            WHERE t.user_id = ? AND l.result_status = 'failed'
            ORDER BY l.id DESC LIMIT ?
            """,
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_due_tasks(self) -> List[TaskDTO]:
        cursor = await self._db.execute("""
            SELECT * FROM tasks
            WHERE enabled = 1 AND check_interval_sec > 0 AND status != 'running'
            """)
        rows = await cursor.fetchall()
        now = datetime.now(timezone.utc)
        due: List[TaskDTO] = []
        for row in rows:
            task = _row_to_task(row)
            if not task.last_checked_at:
                due.append(task)
                continue
            last_dt = datetime.fromisoformat(task.last_checked_at)
            if (now - last_dt).total_seconds() >= task.check_interval_sec:
                due.append(task)
        return due

    async def prune_old_logs(self, retention_days: int = 30) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()
        cursor = await self._db.execute(
            "DELETE FROM execution_logs WHERE executed_at < ?",
            (cutoff,),
        )
        await self._db.commit()
        return cursor.rowcount

    async def get_task_stats(self, task_id: int, user_id: int) -> Dict[str, Any]:
        since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        cursor = await self._db.execute(
            """
            SELECT l.result_status, l.latency_ms
            FROM execution_logs l
            JOIN tasks t ON l.task_id = t.id
            WHERE l.task_id = ? AND t.user_id = ? AND l.executed_at >= ?
            """,
            (task_id, user_id, since_24h),
        )
        rows = await cursor.fetchall()
        total = len(rows)
        if not total:
            return {
                "checks_24h": 0,
                "uptime_24h_pct": 100.0,
                "avg_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
            }
        lats = [float(r["latency_ms"]) for r in rows]
        ok_cnt = sum(1 for r in rows if r["result_status"] == "completed")
        return {
            "checks_24h": total,
            "uptime_24h_pct": round((ok_cnt / total) * 100.0, 2),
            "avg_latency_ms": round(sum(lats) / total, 2),
            "p95_latency_ms": _compute_p95(lats),
        }

    async def get_analytics_summary(self, user_id: int) -> Dict[str, Any]:
        cursor = await self._db.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0)
                    AS completed,
                COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0)
                    AS pending,
                COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0)
                    AS failed,
                COALESCE(SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END), 0)
                    AS running
            FROM tasks WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        log_metrics = await self._get_recent_log_metrics(user_id)
        return {
            "total": int(row["total"]),
            "completed": int(row["completed"]),
            "pending": int(row["pending"]),
            "failed": int(row["failed"]),
            "running": int(row["running"]),
            **log_metrics,
        }

    async def _get_recent_log_metrics(self, user_id: int) -> Dict[str, Any]:
        since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        cursor_logs = await self._db.execute(
            """
            SELECT l.result_status, l.latency_ms, l.suppressed
            FROM execution_logs l
            JOIN tasks t ON l.task_id = t.id
            WHERE t.user_id = ? AND l.executed_at >= ?
            """,
            (user_id, since_24h),
        )
        recent_logs = await cursor_logs.fetchall()
        total_24h = len(recent_logs)
        if not total_24h:
            return {
                "avg_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "uptime_24h_pct": 100.0,
                "checks_24h": 0,
                "suppressed_24h": 0,
            }
        latencies = [float(r["latency_ms"]) for r in recent_logs]
        ok_24h = sum(1 for r in recent_logs if r["result_status"] == "completed")
        return {
            "avg_latency_ms": round(sum(latencies) / total_24h, 2),
            "p95_latency_ms": _compute_p95(latencies),
            "uptime_24h_pct": round((ok_24h / total_24h) * 100.0, 2),
            "checks_24h": total_24h,
            "suppressed_24h": sum(1 for r in recent_logs if r["suppressed"]),
        }

import aiosqlite
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from src.models import UserDTO, TaskDTO, ExecutionLogDTO


class DatabaseRepository:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def init_db(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        await self._db.execute("PRAGMA foreign_keys = ON;")
        await self._db.execute("PRAGMA cache_size=-64000;")
        await self._db.execute("PRAGMA temp_store=MEMORY;")
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
                executed_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_execution_logs_task_id ON execution_logs(task_id);"
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

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
            INSERT INTO tasks (user_id, title, category, target_endpoint, priority, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                task.user_id,
                task.title,
                task.category,
                task.target_endpoint,
                task.priority,
                task.status,
                now,
                now,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_task_by_id(self, task_id: int) -> Optional[TaskDTO]:
        self._db.row_factory = aiosqlite.Row
        cursor = await self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return TaskDTO(**dict(row))

    async def get_user_tasks(
        self, user_id: int, status_filter: Optional[str] = None
    ) -> List[TaskDTO]:
        self._db.row_factory = aiosqlite.Row
        if status_filter:
            cursor = await self._db.execute(
                "SELECT * FROM tasks WHERE user_id = ? AND status = ? ORDER BY priority DESC, id DESC",
                (user_id, status_filter),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM tasks WHERE user_id = ? ORDER BY priority DESC, id DESC",
                (user_id,),
            )
        rows = await cursor.fetchall()
        return [TaskDTO(**dict(r)) for r in rows]

    async def update_task_status(self, task_id: int, new_status: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, task_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def update_task_priority(self, task_id: int, new_priority: int) -> bool:
        if new_priority not in (1, 2, 3):
            raise ValueError("Приоритет должен быть в диапазоне от 1 до 3")
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "UPDATE tasks SET priority = ?, updated_at = ? WHERE id = ?",
            (new_priority, now, task_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def delete_task(self, task_id: int) -> bool:
        cursor = await self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def add_execution_log(self, log_entry: ExecutionLogDTO) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            """
            INSERT INTO execution_logs (task_id, result_status, latency_ms, details, executed_at)
            VALUES (?, ?, ?, ?, ?)
        """,
            (
                log_entry.task_id,
                log_entry.result_status,
                log_entry.latency_ms,
                log_entry.details,
                now,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_task_logs(
        self, task_id: int, limit: int = 5
    ) -> List[ExecutionLogDTO]:
        self._db.row_factory = aiosqlite.Row
        cursor = await self._db.execute(
            "SELECT * FROM execution_logs WHERE task_id = ? ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        )
        rows = await cursor.fetchall()
        return [ExecutionLogDTO(**dict(r)) for r in rows]

    async def get_analytics_summary(self, user_id: int) -> Dict[str, Any]:
        cursor = await self._db.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
            FROM tasks WHERE user_id = ?
        """,
            (user_id,),
        )
        row = await cursor.fetchone()
        cursor_lat = await self._db.execute(
            """
            SELECT AVG(l.latency_ms)
            FROM execution_logs l
            JOIN tasks t ON l.task_id = t.id
            WHERE t.user_id = ?
        """,
            (user_id,),
        )
        avg_lat = (await cursor_lat.fetchone())[0]
        return {
            "total": row[0] or 0,
            "completed": row[1] or 0,
            "pending": row[2] or 0,
            "failed": row[3] or 0,
            "avg_latency_ms": round(avg_lat, 2) if avg_lat is not None else 0.0,
        }

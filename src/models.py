from dataclasses import dataclass
from typing import Optional


@dataclass
class UserDTO:
    id: int
    username: str
    full_name: str
    role: str = "user"
    created_at: Optional[str] = None


@dataclass
class TaskDTO:
    id: Optional[int]
    user_id: int
    title: str
    category: str
    target_endpoint: str
    priority: int = 2
    status: str = "pending"
    check_interval_sec: int = 0
    expected_status: int = 200
    expected_text: str = ""
    ssl_min_days: int = 14
    enabled: bool = True
    consecutive_failures: int = 0
    alert_threshold: int = 2
    last_checked_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class ExecutionLogDTO:
    id: Optional[int]
    task_id: int
    result_status: str
    latency_ms: float
    details: str
    status_code: Optional[int] = None
    ssl_days_left: Optional[int] = None
    executed_at: Optional[str] = None


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    details: str
    status_code: Optional[int] = None
    ssl_days_left: Optional[int] = None

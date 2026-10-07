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
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class ExecutionLogDTO:
    id: Optional[int]
    task_id: int
    result_status: str
    latency_ms: float
    details: str
    executed_at: Optional[str] = None

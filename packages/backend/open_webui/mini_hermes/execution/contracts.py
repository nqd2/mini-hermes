"""Versioned execution contracts shared by application services and adapters."""

import time
from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol

TaskStatus = Literal['ready', 'in_progress', 'pending', 'completed', 'partial', 'blocked', 'cancelled']


@dataclass(frozen=True)
class TaskRequirements:
    goal: str
    actions: tuple[str, ...] = ('read',)
    complete_coverage: bool = False


@dataclass(frozen=True)
class EvidenceReference:
    evidence_id: str
    resource_id: str
    revision: int
    start: int
    end: int


@dataclass(frozen=True)
class Coverage:
    complete: bool = False
    resource_ids: tuple[str, ...] = ()


@dataclass
class ExecutionBudget:
    deadline: float = field(default_factory=lambda: time.time() + 300)
    model_calls: int = 0
    reads: int = 0
    max_model_calls: int = 8
    max_reads: int = 24

    def exhausted(self) -> bool:
        return time.time() >= self.deadline or self.model_calls >= self.max_model_calls or self.reads >= self.max_reads

    def consume_model_call(self) -> bool:
        if time.time() >= self.deadline or self.model_calls >= self.max_model_calls:
            return False
        self.model_calls += 1
        return True

    def consume_read(self) -> bool:
        if time.time() >= self.deadline or self.reads >= self.max_reads:
            return False
        self.reads += 1
        return True

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class CompletionDecision:
    status: TaskStatus
    reason: str = ''
    coverage_complete: bool = False

    def as_dict(self):
        return {'schema_version': 1, **asdict(self)}


class ResourceIndexPending(RuntimeError):
    """The resource exists, but its current revision is not searchable yet."""

    def __init__(self, job_ids: list[str]):
        self.job_ids = tuple(job_ids)
        super().__init__('Resource indexing is pending')


class ResourceIndexFailed(RuntimeError):
    """The resource exists, but indexing exhausted its retry budget."""

    def __init__(self, job_ids: list[str]):
        self.job_ids = tuple(job_ids)
        super().__init__('Resource indexing failed')


class ResourceSearch(Protocol):
    async def __call__(
        self, user_id: str, files: list[dict], chat_id: str | None, query: str, **options
    ) -> list[dict]: ...

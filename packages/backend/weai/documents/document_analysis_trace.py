"""Per-step user-visible records, separate from provider tool output."""

from dataclasses import dataclass, field
from hashlib import sha256
import inspect
from threading import Lock
from uuid import uuid4

from sqlalchemy import select

from .models import ResourceAnalysisTrace, ResourceTask, now

MAX_STEP_OUTPUT = 4096
MAX_TRACE_EVENTS = 16000
MAX_PAGE = 25


class AnalysisTraceStopped(Exception):
    """The worker must stop before starting another operation."""


async def current_lease(db, task_id, lease):
    task = await db.get(ResourceTask, task_id, with_for_update=True, populate_existing=True)
    if task and task.status == 'processing' and lease and task.lease_until == lease and lease > now():
        return task
    return None


async def _latest(db, task_id):
    return await db.scalar(select(ResourceAnalysisTrace).where(
        ResourceAnalysisTrace.task_id == task_id,
    ).order_by(ResourceAnalysisTrace.sequence.desc()).limit(1))


def _append(db, task_id, sequence, step_id, kind, data):
    db.add(ResourceAnalysisTrace(task_id=task_id, sequence=sequence, step_id=step_id, kind=kind, data=data))


def _source(fn, implementation):
    from . import document_analysis

    if fn not in (document_analysis.survey_document, document_analysis.ocr_unit):
        return {}
    try:
        source = inspect.getsource(fn)
        if implementation in (
            document_analysis._workbook, document_analysis._pdf, document_analysis._word, document_analysis._slides,
        ):
            source += '\n\n' + inspect.getsource(implementation)
    except (OSError, TypeError):
        return {'function': fn.__name__}
    return {
        'function': fn.__name__, 'code': source[:4800],
        'source_version': sha256(source.encode()).hexdigest()[:12],
        'code_truncated': len(source) > 4800,
    }


@dataclass
class AnalysisStepTrace:
    task_id: str
    lease: object
    step_id: str = field(default_factory=lambda: str(uuid4()))
    counts: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)
    truncated: set = field(default_factory=set)
    lock: Lock = field(default_factory=Lock)
    flush_count: int = 0

    def emit(self, channel, text):
        # Callers emit reviewed, structured diagnostics (counts, flags and error
        # categories). Never route global stdout, raw exception strings or input files here.
        if channel not in ('stdout', 'stderr') or not text:
            return
        with self.lock:
            room = MAX_STEP_OUTPUT - self.counts.get(channel, 0)
            if len(text) > room:
                self.truncated.add(channel)
            if room > 0:
                chunk = text[:room]
                self.pending[channel] = self.pending.get(channel, '') + chunk
                self.counts[channel] = self.counts.get(channel, 0) + len(chunk)

    def _drain(self):
        with self.lock:
            messages, self.pending = self.pending, {}
            return messages.items()

    async def flush(self, db):
        if self.flush_count >= 2 or not self.pending:
            return
        task = await current_lease(db, self.task_id, self.lease)
        if not task:
            await db.rollback()
            return
        if task.cancel_requested:
            await db.commit()
            return
        await self._write(db, self._drain())
        await db.commit()
        self.flush_count += 1

    async def _write(self, db, messages, *, terminal=None):
        latest = await _latest(db, self.task_id)
        if not latest or latest.step_id != self.step_id or latest.kind == 'finished':
            return
        seq = latest.sequence
        for channel, text in messages:
            seq += 1
            _append(db, self.task_id, seq, self.step_id, channel, {'text': text})
        if terminal:
            _append(db, self.task_id, seq + 1, self.step_id, 'finished', {
                **terminal, 'truncated': sorted(self.truncated),
            })

    async def finish(self, db, status, elapsed_ms):
        # The caller holds the lease row lock and publishes the step with its checkpoint.
        await self._write(db, self._drain(), terminal={'status': status, 'elapsed_ms': elapsed_ms})


async def start_analysis_trace(db, task_id, lease, phase, *, function=None, implementation=None, scope=None):
    task = await current_lease(db, task_id, lease)
    if not task:
        await db.rollback()
        raise AnalysisTraceStopped
    if task.cancel_requested:
        raise AnalysisTraceStopped
    latest = await _latest(db, task_id)
    seq = latest.sequence if latest else 0
    if seq >= MAX_TRACE_EVENTS - 9:
        await db.commit()
        return None
    if latest and latest.kind != 'finished':
        seq += 1
        _append(db, task_id, seq, latest.step_id, 'finished', {
            'status': 'interrupted', 'truncated': [],
        })
    trace = AnalysisStepTrace(task_id, lease)
    _append(db, task_id, seq + 1, trace.step_id, 'started', {
        'phase': phase, 'revision': task.revision, 'scope': scope, **_source(function, implementation),
    })
    await db.commit()
    return trace


async def finish_abandoned_trace(db, task_id, status):
    latest = await _latest(db, task_id)
    if latest and latest.kind != 'finished':
        _append(db, task_id, latest.sequence + 1, latest.step_id, 'finished', {
            'status': 'cancelled' if status == 'cancelled' else 'interrupted', 'truncated': [],
        })


async def read_analysis_trace(db, task_id, after, limit=MAX_PAGE):
    limit = min(MAX_PAGE, limit)
    rows = (await db.scalars(select(ResourceAnalysisTrace).where(
        ResourceAnalysisTrace.task_id == task_id, ResourceAnalysisTrace.sequence > after,
    ).order_by(ResourceAnalysisTrace.sequence).limit(limit + 1))).all()
    page = rows[:limit]
    return {
        'events': [{
            'sequence': row.sequence, 'step_id': row.step_id, 'kind': row.kind,
            'data': row.data, 'at': row.created_at.isoformat(),
        } for row in page],
        'next_after': page[-1].sequence if page else after,
        'has_more': len(rows) > limit,
    }

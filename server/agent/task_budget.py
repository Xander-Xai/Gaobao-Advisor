"""Unified task-level resource budget for the Gaobao-Advisor agent runtime.

Governance boundary
-------------------
The platform-wide model governance layer (referred to as GA01 / "LLM Resource
Governor") owns the *global* model resource envelope: request admission,
cross-request concurrency and total platform spend. It is deliberately **not**
re-implemented here.

This module owns the *task-level* envelope: how much one admitted user
request or one agent task may consume before it must terminate, degrade or
return partial results::

    GA01 (global admission)  ->  TaskBudget (per task)
                                              |- max_model_calls
                                              |- max_tool_calls
                                              |- max_steps
                                              |- max_input_tokens
                                              |- max_output_tokens
                                              |- max_total_tokens
                                              |- max_retries
                                              |- deadline
                                              |- max_estimated_cost

Invariants
----------
1. *Reserve before execution.*  Every model call, tool call, agent step and
   retry reserves its worst-case consumption **before** the provider is
   contacted.  Concurrent callers therefore cannot collectively exceed a
   limit: the reservation is a single atomic check-and-commit.
2. *Conservative settlement.*  A provider failure whose billing outcome is
   unknown keeps the reserved estimate.  Consumption is never restored to
   zero on an unknown-billing failure.
3. *Shared parent budget.*  Sub-agents, delegated researchers and parallel
   judge dimensions receive a *child view* of the parent ledger, never a
   fresh allowance.
4. *Durable for persisted tasks.*  A crashed task that is reclaimed resumes
   with its already-consumed budget.
5. *Auditable.*  Every attempt produces a usage record that separates
   estimated tokens, provider-reported usage, estimated cost, verified cost
   and unknown cost.
6. *Honest about money.*  A locally computed cost is an *estimate* built from
   a self-declared rate card.  It is never presented as a provider invoice.

A budget never promises to undo a request that was already handed to an
external provider: the consumption is recorded, not clawed back.
"""

from __future__ import annotations

import contextlib
import contextvars
import enum
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

__all__ = [
    "BudgetExhausted",
    "CostStatus",
    "Reservation",
    "TaskBudget",
    "TaskBudgetError",
    "TaskBudgetLimits",
    "TaskCancelled",
    "TaskDeadlineExceeded",
    "TerminationReason",
    "UsageKind",
    "UsageRecord",
    "current_task_budget",
    "use_task_budget",
]


# ── Errors ──────────────────────────────────────────────────────────


class TaskBudgetError(RuntimeError):
    """Base class for every task-level budget enforcement failure."""


class BudgetExhausted(TaskBudgetError):
    """A configured task budget limit was reached.

    The offending attempt was *not* executed.  Whatever the provider had
    already been asked for earlier in the task stays consumed.
    """

    def __init__(self, limit_name: str, limit: Any, observed: Any, scope: str = "root") -> None:
        super().__init__(f"Task budget exhausted: {limit_name}")
        self.limit_name = limit_name
        self.limit = limit
        self.observed = observed
        self.scope = scope


class TaskCancelled(TaskBudgetError):
    """The task was cancelled; no further model call may be started."""


class TaskDeadlineExceeded(TaskBudgetError):
    """The task's wall-clock deadline passed."""


# ── Enumerations ────────────────────────────────────────────────────


class TerminationReason(str, enum.Enum):
    """Why a task stopped.

    ``COMPLETED`` and ``TASK_FAILED`` are *outcomes*; the remaining values
    are *governance terminations* and must never be reported as a model
    quality result.
    """

    COMPLETED = "COMPLETED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CANCELLED = "CANCELLED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    TASK_FAILED = "TASK_FAILED"


class UsageKind(str, enum.Enum):
    MODEL = "model"
    TOOL = "tool"
    STEP = "step"
    RETRY = "retry"


class CostStatus(str, enum.Enum):
    """How much is known about the money a call consumed.

    ``ESTIMATED``
        Computed locally from a self-declared rate card. Not an invoice.
    ``VERIFIED``
        The provider itself reported the charged amount.
    ``UNKNOWN``
        No usable price card, or the billing outcome of a failed call is
        unknown.  Consumption is still recorded in tokens.
    """

    ESTIMATED = "ESTIMATED"
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"


# ── Limits ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TaskBudgetLimits:
    """Hard ceilings for one task.

    ``max_estimated_cost_usd`` only bites when a price card is configured for
    the model in use.  Without a price card the cost of a call is
    ``UNKNOWN`` and the token ceilings remain the effective guard.
    """

    max_model_calls: int = 12
    max_tool_calls: int = 0
    max_steps: int = 12
    max_input_tokens: int = 120_000
    max_output_tokens: int = 16_000
    max_total_tokens: int = 136_000
    max_retries: int = 3
    deadline_seconds: float = 120.0
    max_estimated_cost_usd: Decimal | None = Decimal("0.50")

    def __post_init__(self) -> None:
        for name in (
            "max_model_calls",
            "max_tool_calls",
            "max_steps",
            "max_input_tokens",
            "max_output_tokens",
            "max_total_tokens",
            "max_retries",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if type(self.deadline_seconds) not in {int, float} or self.deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be a positive number")
        if self.max_estimated_cost_usd is not None:
            if not isinstance(self.max_estimated_cost_usd, Decimal):
                raise ValueError("max_estimated_cost_usd must be a Decimal")
            if self.max_estimated_cost_usd <= 0:
                raise ValueError("max_estimated_cost_usd must be positive")


# ── Profiles ────────────────────────────────────────────────────────
#
# Ordinary consultation and complex research deliberately get *different*
# budgets.  A single shared ceiling would either starve the research path or
# leave the consultation path unbounded.

CONSULTATION_LIMITS = TaskBudgetLimits(
    max_model_calls=12,
    max_tool_calls=0,
    max_steps=12,
    max_input_tokens=120_000,
    max_output_tokens=16_000,
    max_total_tokens=136_000,
    max_retries=3,
    deadline_seconds=120.0,
    max_estimated_cost_usd=Decimal("0.50"),
)

RESEARCH_LIMITS = TaskBudgetLimits(
    max_model_calls=48,
    max_tool_calls=32,
    max_steps=96,
    max_input_tokens=1_200_000,
    max_output_tokens=96_000,
    max_total_tokens=1_296_000,
    max_retries=6,
    deadline_seconds=900.0,
    max_estimated_cost_usd=Decimal("8.00"),
)

TASKRUN_LIMITS = TaskBudgetLimits(
    max_model_calls=12,
    max_tool_calls=12,
    max_steps=16,
    max_input_tokens=120_000,
    max_output_tokens=12_000,
    max_total_tokens=132_000,
    max_retries=2,
    deadline_seconds=600.0,
    max_estimated_cost_usd=Decimal("1.00"),
)

_PROFILES: dict[str, TaskBudgetLimits] = {
    "consultation": CONSULTATION_LIMITS,
    "research": RESEARCH_LIMITS,
    "taskrun": TASKRUN_LIMITS,
}


def _env_int(name: str, current: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return current
    try:
        value = int(raw)
    except ValueError:
        return current
    return value if value >= 0 else current


def _env_float(name: str, current: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return current
    try:
        value = float(raw)
    except ValueError:
        return current
    return value if value > 0 else current


def _env_decimal(name: str, current: Decimal | None) -> Decimal | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return current
    try:
        value = Decimal(raw)
    except Exception:
        return current
    return value if value > 0 else current


def resolve_profile_limits(profile: str) -> TaskBudgetLimits:
    """Return the limits of *profile*, with the documented operator overrides.

    The overrides exist so an operator can tighten a ceiling without a code
    change.  They can only lower or raise within the declared field types;
    an unparsable value is ignored rather than silently disabling a limit.
    """
    if profile not in _PROFILES:
        raise ValueError(f"Unknown task budget profile: {profile}")
    base = _PROFILES[profile]
    return replace(
        base,
        max_model_calls=_env_int("GAOBAO_TASK_BUDGET_MAX_MODEL_CALLS", base.max_model_calls),
        max_total_tokens=_env_int("GAOBAO_TASK_BUDGET_MAX_TOTAL_TOKENS", base.max_total_tokens),
        max_retries=_env_int("GAOBAO_TASK_BUDGET_MAX_RETRIES", base.max_retries),
        deadline_seconds=_env_float("GAOBAO_TASK_BUDGET_DEADLINE_SECONDS", base.deadline_seconds),
        max_estimated_cost_usd=_env_decimal(
            "GAOBAO_TASK_BUDGET_MAX_ESTIMATED_COST_USD", base.max_estimated_cost_usd
        ),
    )


# ── Usage records ───────────────────────────────────────────────────


@dataclass
class UsageRecord:
    """One auditable attempt against a task budget."""

    sequence: int
    scope: str
    kind: str
    model: str | None
    estimated_input_tokens: int
    estimated_output_tokens: int
    actual_input_tokens: int | None = None
    actual_output_tokens: int | None = None
    provider_usage_known: bool = False
    provider_billing_status: str = "UNKNOWN"
    estimated_cost_usd: Decimal | None = None
    verified_cost_usd: Decimal | None = None
    cost_status: str = CostStatus.UNKNOWN.value
    pricing: dict[str, Any] | None = None
    status: str = "RESERVED"
    error_type: str | None = None
    started_at: float = 0.0
    ended_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "scope": self.scope,
            "kind": self.kind,
            "model": self.model,
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "actual_input_tokens": self.actual_input_tokens,
            "actual_output_tokens": self.actual_output_tokens,
            "provider_usage_known": self.provider_usage_known,
            "provider_billing_status": self.provider_billing_status,
            "estimated_cost_usd": _money(self.estimated_cost_usd),
            "verified_cost_usd": _money(self.verified_cost_usd),
            "cost_status": self.cost_status,
            "pricing": self.pricing,
            "status": self.status,
            "error_type": self.error_type,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


@dataclass(frozen=True)
class Reservation:
    """A granted, already-counted claim on the budget.

    The reservation is charged against the ledger at grant time.  Settlement
    only *adjusts* it, so a crash between grant and settlement can never
    hand the same allowance out twice.
    """

    sequence: int
    scope: str
    kind: str
    model: str | None
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: Decimal | None
    pricing: dict[str, Any] | None
    started_at: float


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:.8f}"


# ── Ledger ──────────────────────────────────────────────────────────


@dataclass
class _Ledger:
    """Mutable, shared consumption state.

    One ledger belongs to exactly one task.  Child budgets are *views* onto
    the same ledger, which is what makes "sub-agents share the parent
    budget" true rather than aspirational.
    """

    limits: TaskBudgetLimits
    started_at: float
    deadline_at: float
    price_book: Any = None
    lock: threading.RLock = field(default_factory=threading.RLock)
    cancelled: bool = False
    cancel_reason: str | None = None
    model_calls: int = 0
    tool_calls: int = 0
    steps: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: Decimal = Decimal("0")
    verified_cost: Decimal = Decimal("0")
    unknown_cost_calls: int = 0
    termination_reason: str | None = None
    records: list[UsageRecord] = field(default_factory=list)
    _sequence: int = 0
    on_settle: Callable[[_Ledger], None] | None = None

    # -- snapshot -----------------------------------------------------

    def to_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "schema": "gaobao/task-budget/v1",
                "limits": {
                    "max_model_calls": self.limits.max_model_calls,
                    "max_tool_calls": self.limits.max_tool_calls,
                    "max_steps": self.limits.max_steps,
                    "max_input_tokens": self.limits.max_input_tokens,
                    "max_output_tokens": self.limits.max_output_tokens,
                    "max_total_tokens": self.limits.max_total_tokens,
                    "max_retries": self.limits.max_retries,
                    "deadline_seconds": self.limits.deadline_seconds,
                    "max_estimated_cost_usd": _money(self.limits.max_estimated_cost_usd),
                },
                "started_at": self.started_at,
                "deadline_at": self.deadline_at,
                "cancelled": self.cancelled,
                "cancel_reason": self.cancel_reason,
                "model_calls": self.model_calls,
                "tool_calls": self.tool_calls,
                "steps": self.steps,
                "retries": self.retries,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "estimated_cost_usd": _money(self.estimated_cost),
                "verified_cost_usd": _money(self.verified_cost),
                "unknown_cost_calls": self.unknown_cost_calls,
                "termination_reason": self.termination_reason,
                "records": [record.to_dict() for record in self.records],
            }

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        *,
        price_book: Any = None,
        on_settle: Callable[[_Ledger], None] | None = None,
    ) -> _Ledger:
        """Rebuild a ledger from a persisted snapshot.

        Used when a persisted task is reclaimed after a crash: the recovered
        task continues with the budget it had already consumed.
        """
        if not isinstance(snapshot, dict) or snapshot.get("schema") != "gaobao/task-budget/v1":
            raise ValueError("Unsupported task budget snapshot")
        raw_limits = snapshot.get("limits")
        if not isinstance(raw_limits, dict):
            raise ValueError("Task budget snapshot has no limits")
        cost = raw_limits.get("max_estimated_cost_usd")
        limits = TaskBudgetLimits(
            max_model_calls=int(raw_limits["max_model_calls"]),
            max_tool_calls=int(raw_limits["max_tool_calls"]),
            max_steps=int(raw_limits["max_steps"]),
            max_input_tokens=int(raw_limits["max_input_tokens"]),
            max_output_tokens=int(raw_limits["max_output_tokens"]),
            max_total_tokens=int(raw_limits["max_total_tokens"]),
            max_retries=int(raw_limits["max_retries"]),
            deadline_seconds=float(raw_limits["deadline_seconds"]),
            max_estimated_cost_usd=Decimal(cost) if cost else None,
        )
        ledger = cls(
            limits=limits,
            started_at=float(snapshot.get("started_at") or time.time()),
            deadline_at=float(snapshot.get("deadline_at") or time.time()),
            price_book=price_book,
            on_settle=on_settle,
            cancelled=bool(snapshot.get("cancelled")),
            cancel_reason=snapshot.get("cancel_reason"),
            model_calls=int(snapshot.get("model_calls") or 0),
            tool_calls=int(snapshot.get("tool_calls") or 0),
            steps=int(snapshot.get("steps") or 0),
            retries=int(snapshot.get("retries") or 0),
            input_tokens=int(snapshot.get("input_tokens") or 0),
            output_tokens=int(snapshot.get("output_tokens") or 0),
            estimated_cost=Decimal(snapshot.get("estimated_cost_usd") or "0"),
            verified_cost=Decimal(snapshot.get("verified_cost_usd") or "0"),
            unknown_cost_calls=int(snapshot.get("unknown_cost_calls") or 0),
            termination_reason=snapshot.get("termination_reason"),
        )
        for raw in snapshot.get("records") or []:
            ledger.records.append(
                UsageRecord(
                    sequence=int(raw.get("sequence") or 0),
                    scope=str(raw.get("scope") or "root"),
                    kind=str(raw.get("kind") or UsageKind.MODEL.value),
                    model=raw.get("model"),
                    estimated_input_tokens=int(raw.get("estimated_input_tokens") or 0),
                    estimated_output_tokens=int(raw.get("estimated_output_tokens") or 0),
                    actual_input_tokens=raw.get("actual_input_tokens"),
                    actual_output_tokens=raw.get("actual_output_tokens"),
                    provider_usage_known=bool(raw.get("provider_usage_known")),
                    provider_billing_status=str(raw.get("provider_billing_status") or "UNKNOWN"),
                    estimated_cost_usd=(
                        Decimal(raw["estimated_cost_usd"]) if raw.get("estimated_cost_usd") else None
                    ),
                    verified_cost_usd=(
                        Decimal(raw["verified_cost_usd"]) if raw.get("verified_cost_usd") else None
                    ),
                    cost_status=str(raw.get("cost_status") or CostStatus.UNKNOWN.value),
                    pricing=raw.get("pricing"),
                    status=str(raw.get("status") or "RESERVED"),
                    error_type=raw.get("error_type"),
                    started_at=float(raw.get("started_at") or 0.0),
                    ended_at=raw.get("ended_at"),
                )
            )
        ledger._sequence = max((record.sequence for record in ledger.records), default=0)
        return ledger


# ── Budget ──────────────────────────────────────────────────────────


class TaskBudget:
    """The task-level resource budget.

    A budget is safe to share between threads and asyncio tasks: all state
    transitions happen under one lock.
    """

    def __init__(
        self,
        limits: TaskBudgetLimits,
        *,
        scope: str = "root",
        ledger: _Ledger | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        if ledger is None:
            now = clock()
            ledger = _Ledger(
                limits=limits,
                started_at=now,
                deadline_at=now + limits.deadline_seconds,
            )
        self._ledger = ledger
        self._scope = scope

    # -- construction -------------------------------------------------

    @classmethod
    def for_profile(
        cls,
        profile: str,
        *,
        scope: str = "root",
        clock: Callable[[], float] = time.time,
        price_book: Any = None,
    ) -> TaskBudget:
        """Create a budget from a named profile.

        ``consultation`` is the ordinary user question, ``research`` the
        multi-agent admissions research, ``taskrun`` the persisted operator
        TaskRun.
        """
        limits = resolve_profile_limits(profile)
        now = clock()
        ledger = _Ledger(
            limits=limits,
            started_at=now,
            deadline_at=now + limits.deadline_seconds,
            price_book=price_book,
        )
        return cls(limits, scope=scope, ledger=ledger, clock=clock)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        *,
        scope: str = "root",
        clock: Callable[[], float] = time.time,
        price_book: Any = None,
        on_settle: Callable[[_Ledger], None] | None = None,
    ) -> TaskBudget:
        """Rebuild a budget from a persisted snapshot (crash recovery)."""
        ledger = _Ledger.from_snapshot(snapshot, price_book=price_book, on_settle=on_settle)
        return cls(ledger.limits, scope=scope, ledger=ledger, clock=clock)

    def child(self, scope: str) -> TaskBudget:
        """Return a child view that *shares* this budget.

        The child gets its own attribution label for the audit trail, but it
        draws from the same counters and the same limits.  A delegated
        sub-agent therefore cannot obtain a second full allowance.
        """
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("Child scope must be a non-empty string")
        return TaskBudget(self._ledger.limits, scope=scope, ledger=self._ledger, clock=self._clock)

    def bind_persistence(
        self,
        *,
        on_settle: Callable[[_Ledger], None] | None = None,
        price_book: Any = None,
    ) -> None:
        """Attach a persistence callback and (optionally) a price book."""
        with self._ledger.lock:
            if on_settle is not None:
                self._ledger.on_settle = on_settle
            if price_book is not None:
                self._ledger.price_book = price_book

    # -- properties ---------------------------------------------------

    @property
    def limits(self) -> TaskBudgetLimits:
        return self._ledger.limits

    @property
    def scope(self) -> str:
        return self._scope

    @property
    def terminated(self) -> bool:
        return self._ledger.termination_reason is not None

    @property
    def termination_reason(self) -> str | None:
        return self._ledger.termination_reason

    # -- guards -------------------------------------------------------

    def _guard(self) -> None:
        """Raise if the task must not start another unit of work."""
        ledger = self._ledger
        if ledger.cancelled:
            raise TaskCancelled(ledger.cancel_reason or "Task cancelled")
        if self._clock() >= ledger.deadline_at:
            ledger.termination_reason = TerminationReason.DEADLINE_EXCEEDED.value
            raise TaskDeadlineExceeded("Task deadline exceeded")
        if ledger.termination_reason is not None:
            raise BudgetExhausted(
                "termination", ledger.termination_reason, ledger.termination_reason, self._scope
            )

    def _exhaust(self, name: str, limit: Any, observed: Any) -> BudgetExhausted:
        self._ledger.termination_reason = TerminationReason.BUDGET_EXHAUSTED.value
        return BudgetExhausted(name, limit, observed, self._scope)

    # -- reservations -------------------------------------------------

    def reserve_model_call(
        self,
        *,
        estimated_input_tokens: int = 0,
        estimated_output_tokens: int = 0,
        model: str | None = None,
        retry: bool = False,
    ) -> Reservation:
        """Reserve one model call against the budget.

        The reservation is granted only if *every* ceiling still holds after
        the call.  The check and the commit happen under one lock, so
        parallel callers can never collectively exceed a limit.
        """
        est_in = max(0, int(estimated_input_tokens))
        est_out = max(0, int(estimated_output_tokens))
        ledger = self._ledger
        with ledger.lock:
            self._guard()
            limits = ledger.limits
            if ledger.model_calls + 1 > limits.max_model_calls:
                raise self._exhaust("max_model_calls", limits.max_model_calls, ledger.model_calls + 1)
            if retry:
                if ledger.retries + 1 > limits.max_retries:
                    raise self._exhaust("max_retries", limits.max_retries, ledger.retries + 1)
            if ledger.input_tokens + est_in > limits.max_input_tokens:
                raise self._exhaust(
                    "max_input_tokens", limits.max_input_tokens, ledger.input_tokens + est_in
                )
            if ledger.output_tokens + est_out > limits.max_output_tokens:
                raise self._exhaust(
                    "max_output_tokens", limits.max_output_tokens, ledger.output_tokens + est_out
                )
            total = ledger.input_tokens + ledger.output_tokens + est_in + est_out
            if total > limits.max_total_tokens:
                raise self._exhaust("max_total_tokens", limits.max_total_tokens, total)
            cost, card = self._estimate_cost(model, est_in, est_out)
            if (
                cost is not None
                and limits.max_estimated_cost_usd is not None
                and ledger.estimated_cost + cost > limits.max_estimated_cost_usd
            ):
                raise self._exhaust(
                    "max_estimated_cost_usd",
                    _money(limits.max_estimated_cost_usd),
                    _money(ledger.estimated_cost + cost),
                )

            # Commit the reservation.
            ledger.model_calls += 1
            if retry:
                ledger.retries += 1
            ledger.input_tokens += est_in
            ledger.output_tokens += est_out
            if cost is not None:
                ledger.estimated_cost += cost
            ledger._sequence += 1
            pricing = _pricing_meta(card)
            record = UsageRecord(
                sequence=ledger._sequence,
                scope=self._scope,
                kind=UsageKind.RETRY.value if retry else UsageKind.MODEL.value,
                model=model,
                estimated_input_tokens=est_in,
                estimated_output_tokens=est_out,
                estimated_cost_usd=cost,
                cost_status=CostStatus.ESTIMATED.value if cost is not None else CostStatus.UNKNOWN.value,
                pricing=pricing,
                status="RESERVED",
                started_at=self._clock(),
            )
            ledger.records.append(record)
            return Reservation(
                sequence=record.sequence,
                scope=record.scope,
                kind=record.kind,
                model=model,
                estimated_input_tokens=est_in,
                estimated_output_tokens=est_out,
                estimated_cost_usd=cost,
                pricing=pricing,
                started_at=record.started_at,
            )

    def reserve_tool_call(self, *, name: str | None = None) -> Reservation:
        """Reserve one tool call against the budget."""
        ledger = self._ledger
        with ledger.lock:
            self._guard()
            limits = ledger.limits
            if ledger.tool_calls + 1 > limits.max_tool_calls:
                raise self._exhaust("max_tool_calls", limits.max_tool_calls, ledger.tool_calls + 1)
            ledger.tool_calls += 1
            ledger._sequence += 1
            record = UsageRecord(
                sequence=ledger._sequence,
                scope=self._scope,
                kind=UsageKind.TOOL.value,
                model=name,
                started_at=self._clock(),
            )
            ledger.records.append(record)
            return Reservation(
                sequence=record.sequence,
                scope=record.scope,
                kind=record.kind,
                model=name,
                estimated_input_tokens=0,
                estimated_output_tokens=0,
                estimated_cost_usd=None,
                pricing=None,
                started_at=record.started_at,
            )

    def reserve_step(self) -> Reservation:
        """Reserve one agent execution step against the budget."""
        ledger = self._ledger
        with ledger.lock:
            self._guard()
            limits = ledger.limits
            if ledger.steps + 1 > limits.max_steps:
                raise self._exhaust("max_steps", limits.max_steps, ledger.steps + 1)
            ledger.steps += 1
            ledger._sequence += 1
            record = UsageRecord(
                sequence=ledger._sequence,
                scope=self._scope,
                kind=UsageKind.STEP.value,
                started_at=self._clock(),
            )
            ledger.records.append(record)
            return Reservation(
                sequence=record.sequence,
                scope=record.scope,
                kind=record.kind,
                model=None,
                estimated_input_tokens=0,
                estimated_output_tokens=0,
                estimated_cost_usd=None,
                pricing=None,
                started_at=record.started_at,
            )

    # -- settlement ---------------------------------------------------

    def settle(
        self,
        reservation: Reservation,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        provider_usage_known: bool = True,
        verified_cost_usd: Decimal | None = None,
        error_type: str | None = None,
    ) -> UsageRecord:
        """Settle a reservation with the provider's reported usage.

        When the provider's usage is unknown the reserved estimate is kept.
        Consumption is never refunded on an unknown-billing failure.
        """
        ledger = self._ledger
        with ledger.lock:
            record = self._find(reservation)
            if provider_usage_known and input_tokens is not None and output_tokens is not None:
                actual_in = max(0, int(input_tokens))
                actual_out = max(0, int(output_tokens))
                record.actual_input_tokens = actual_in
                record.actual_output_tokens = actual_out
                record.provider_usage_known = True
                record.provider_billing_status = "KNOWN"
                ledger.input_tokens += actual_in - reservation.estimated_input_tokens
                ledger.output_tokens += actual_out - reservation.estimated_output_tokens
                cost, card = self._estimate_cost(record.model, actual_in, actual_out)
                if cost is not None:
                    ledger.estimated_cost += cost - (reservation.estimated_cost_usd or Decimal("0"))
                    record.estimated_cost_usd = cost
                    record.cost_status = CostStatus.ESTIMATED.value
                    record.pricing = _pricing_meta(card)
                else:
                    record.estimated_cost_usd = None
                    record.cost_status = CostStatus.UNKNOWN.value
            else:
                # Conservative: keep the reserved estimate, mark the billing
                # outcome as unknown and never restore consumption to zero.
                record.provider_usage_known = False
                record.provider_billing_status = "UNKNOWN"
                record.cost_status = CostStatus.UNKNOWN.value
                ledger.unknown_cost_calls += 1
            if verified_cost_usd is not None:
                record.verified_cost_usd = verified_cost_usd
                record.cost_status = CostStatus.VERIFIED.value
                ledger.verified_cost += verified_cost_usd
            record.status = "SETTLED" if error_type is None else "FAILED_UNKNOWN_BILLING"
            record.error_type = error_type
            record.ended_at = self._clock()
            self._notify()
            return record

    def fail(self, reservation: Reservation, *, error_type: str) -> UsageRecord:
        """Settle a reservation whose provider call failed.

        The billing outcome of a failed provider call is unknown: the
        reserved estimate stays consumed and the record is flagged as
        ``FAILED_UNKNOWN_BILLING``.
        """
        return self.settle(
            reservation,
            provider_usage_known=False,
            error_type=error_type,
        )

    def _find(self, reservation: Reservation) -> UsageRecord:
        for record in self._ledger.records:
            if record.sequence == reservation.sequence and record.scope == reservation.scope:
                return record
        raise KeyError("Unknown reservation")

    def _notify(self) -> None:
        callback = self._ledger.on_settle
        if callback is None:
            return
        try:
            callback(self._ledger)
        except Exception:
            # A persistence failure must not corrupt the in-memory ledger,
            # but it must not be silent either.
            import logging

            logging.getLogger(__name__).exception("Task budget persistence failed")

    # -- lifecycle ----------------------------------------------------

    def cancel(self, reason: str = "cancelled") -> None:
        """Cancel the task.

        After cancellation no further model call, tool call or step may be
        started.  Work already handed to a provider is not recalled.
        """
        with self._ledger.lock:
            if self._ledger.cancelled:
                return
            self._ledger.cancelled = True
            self._ledger.cancel_reason = reason
            self._ledger.termination_reason = TerminationReason.CANCELLED.value
            self._notify()

    def mark_terminated(self, reason: TerminationReason) -> None:
        with self._ledger.lock:
            if self._ledger.termination_reason is None:
                self._ledger.termination_reason = reason.value
                self._notify()

    # -- reporting ----------------------------------------------------

    def _estimate_cost(self, model: str | None, input_tokens: int, output_tokens: int):
        price_book = self._ledger.price_book
        if price_book is None or not model:
            return None, None
        return price_book.estimate(model, input_tokens, output_tokens)

    def snapshot(self) -> dict[str, Any]:
        """Return the auditable task-level usage snapshot."""
        ledger = self._ledger
        with ledger.lock:
            data = ledger.to_snapshot()
            data["scope"] = self._scope
            data["cost_status"] = self._task_cost_status(ledger)
            data["usage"] = {
                "estimated_input_tokens": ledger.input_tokens,
                "estimated_output_tokens": ledger.output_tokens,
                "actual_input_tokens": sum(
                    record.actual_input_tokens or 0 for record in ledger.records
                ),
                "actual_output_tokens": sum(
                    record.actual_output_tokens or 0 for record in ledger.records
                ),
                "model_calls": ledger.model_calls,
                "tool_calls": ledger.tool_calls,
                "steps": ledger.steps,
                "retries": ledger.retries,
            }
            data["limits"] = {
                "max_model_calls": ledger.limits.max_model_calls,
                "max_tool_calls": ledger.limits.max_tool_calls,
                "max_steps": ledger.limits.max_steps,
                "max_input_tokens": ledger.limits.max_input_tokens,
                "max_output_tokens": ledger.limits.max_output_tokens,
                "max_total_tokens": ledger.limits.max_total_tokens,
                "max_retries": ledger.limits.max_retries,
                "deadline_seconds": ledger.limits.deadline_seconds,
                "max_estimated_cost_usd": _money(ledger.limits.max_estimated_cost_usd),
            }
            data["remaining"] = {
                "model_calls": max(0, ledger.limits.max_model_calls - ledger.model_calls),
                "tool_calls": max(0, ledger.limits.max_tool_calls - ledger.tool_calls),
                "steps": max(0, ledger.limits.max_steps - ledger.steps),
                "retries": max(0, ledger.limits.max_retries - ledger.retries),
                "input_tokens": max(0, ledger.limits.max_input_tokens - ledger.input_tokens),
                "output_tokens": max(0, ledger.limits.max_output_tokens - ledger.output_tokens),
                "total_tokens": max(
                    0,
                    ledger.limits.max_total_tokens
                    - ledger.input_tokens
                    - ledger.output_tokens,
                ),
            }
            return data

    @staticmethod
    def _task_cost_status(ledger: _Ledger) -> str:
        if not ledger.records:
            return CostStatus.UNKNOWN.value
        if all(record.cost_status == CostStatus.VERIFIED.value for record in ledger.records):
            return CostStatus.VERIFIED.value
        if any(record.cost_status == CostStatus.UNKNOWN.value for record in ledger.records):
            return CostStatus.UNKNOWN.value
        return CostStatus.ESTIMATED.value


def _pricing_meta(card: Any) -> dict[str, Any] | None:
    if card is None:
        return None
    return {
        "model_id": card.model_id,
        "currency": card.currency,
        "price_card_version": card.version,
        "source": card.source,
        "source_url": card.source_url,
        "as_of_date": card.as_of,
        "verification": card.verification,
    }


# ── Ambient budget (context propagation) ───────────────────────────
#
# The budget is created explicitly by the request or task owner.  The
# context variable only *carries* it across the async/thread boundary so
# that nested helpers (judge dimensions, agents, tool wrappers) reach the
# same ledger without every signature growing another parameter.

_CURRENT: contextvars.ContextVar[TaskBudget | None] = contextvars.ContextVar(
    "gaobao_current_task_budget", default=None
)


def current_task_budget() -> TaskBudget | None:
    """Return the ambient task budget, if any."""
    return _CURRENT.get()


@contextlib.contextmanager
def use_task_budget(budget: TaskBudget | None) -> Iterator[TaskBudget | None]:
    """Install *budget* as the ambient budget for the duration of the block."""
    token = _CURRENT.set(budget)
    try:
        yield budget
    finally:
        _CURRENT.reset(token)

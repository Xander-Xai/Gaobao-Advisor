"""LLM Resource Governor — provider-level admission control for model calls.

The HTTP entry limiter in :mod:`server.middleware.ratelimit` bounds *client*
requests per IP. It cannot bound how many *model* requests one accepted HTTP
request produces: a single consultation may summarise history, call the chat
model, retry it, run a quality judge over several dimensions and repair a
rejected answer. Those internal calls are what actually consume provider
quota, so they need their own budget.

This module owns that internal budget:

* **RPM** — requests per minute for a quota scope.
* **TPM** — tokens per minute for a quota scope. Which tokens a provider
  counts is provider specific, so the counting mode is configurable
  (:class:`TpmCounting`).
* **Max concurrency** — in-flight model calls per quota scope. This is a
  distributed *semaphore with leases*, not a token bucket: a lease has a TTL,
  an expired lease is reclaimed automatically, and a crashed worker can
  therefore never hold a slot forever.
* **Shared pools** — several providers (or several models) can draw from one
  quota pool, which is how one API key with one contract is modelled.

A call is charged against an *account scope* (the provider or the pool the
provider belongs to) and, when the model declares its own budget, an
additional *model scope*. Both are checked and consumed inside one atomic
Redis Lua script, so several API and Worker instances share one budget.

When Redis is unavailable the governor applies an explicit, bounded failure
policy (:class:`RedisFailureMode`) instead of silently allowing unlimited
calls.

The module never sees prompts, completions or credentials. It only sees
provider identity, token counts and timing.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "AdmissionOutcome",
    "GovernanceConfig",
    "GovernorBackend",
    "LLMQuotaExceeded",
    "LLMResourceGovernor",
    "LocalGovernorBackend",
    "ProviderQuota",
    "QuotaScope",
    "RedisFailureMode",
    "RedisGovernorBackend",
    "ResourceLease",
    "ScopeReservation",
    "TpmCounting",
    "WindowMode",
    "get_governor",
    "load_governance_config",
    "reset_governor",
]

#: All quota windows are one minute, matching how providers publish RPM/TPM.
_WINDOW_MS = 60_000


# ── Configuration primitives ────────────────────────────────────────────────


class WindowMode(str, Enum):
    """How a rate window is measured.

    ``SLIDING`` counts what happened in the last N milliseconds and never
    allows a boundary burst. ``FIXED`` counts per aligned window and is
    cheaper, which matches providers that meter in whole minutes.
    """

    SLIDING = "sliding"
    FIXED = "fixed"


class TpmCounting(str, Enum):
    """Which tokens a provider counts against its token-per-minute quota."""

    TOTAL = "total"
    INPUT = "input"
    OUTPUT = "output"


class RedisFailureMode(str, Enum):
    """What happens when the shared Redis backend cannot be reached."""

    #: Reject the model call. The caller degrades to its own fallback answer.
    FAIL_CLOSED = "fail_closed"
    #: Degrade to a bounded in-process governor. Still bounded, but the
    #: budget is no longer shared between instances.
    LOCAL_FALLBACK = "local_fallback"


@dataclass(frozen=True)
class ProviderQuota:
    """Configuration for one provider, pool or model override."""

    rpm: int = 0
    tpm: int = 0
    max_concurrency: int = 0
    rpm_window: WindowMode = WindowMode.SLIDING
    tpm_window: WindowMode = WindowMode.SLIDING
    tpm_counting: TpmCounting = TpmCounting.TOTAL
    fallback: tuple[str, ...] = ()
    pool: str | None = None
    lease_ttl_seconds: float | None = None


@dataclass(frozen=True)
class QuotaScope:
    """One resolved budget a call is charged against."""

    scope_id: str
    rpm: int
    tpm: int
    max_concurrency: int
    rpm_window: WindowMode
    tpm_window: WindowMode
    tpm_counting: TpmCounting
    lease_ttl_seconds: float

    @property
    def limited(self) -> bool:
        return bool(self.rpm or self.tpm or self.max_concurrency)


@dataclass
class GovernanceConfig:
    """Resolved governance configuration."""

    enabled: bool = True
    namespace: str = "gaobao:llm:v1"
    redis_failure_mode: RedisFailureMode = RedisFailureMode.FAIL_CLOSED
    max_wait_seconds: float = 20.0
    default_lease_ttl_seconds: float = 120.0
    default_output_reservation: int = 512
    default_rpm: int = 0
    default_tpm: int = 0
    default_max_concurrency: int = 0
    providers: dict[str, ProviderQuota] = field(default_factory=dict)
    pools: dict[str, ProviderQuota] = field(default_factory=dict)
    models: dict[str, ProviderQuota] = field(default_factory=dict)

    def quota_for(self, provider: str, model: str | None = None) -> ProviderQuota | None:
        if model and model in self.models:
            return self.models[model]
        return self.providers.get(provider)


# ── Public errors and outcomes ─────────────────────────────────────────────


class LLMQuotaExceeded(RuntimeError):
    """The governor refused a model call."""

    def __init__(self, reason: str, *, provider: str = "", retry_after: float = 0.0) -> None:
        super().__init__(reason)
        self.reason = reason
        self.provider = provider
        self.retry_after = max(0.0, retry_after)


@dataclass(frozen=True)
class AdmissionOutcome:
    """Result of one atomic admission attempt."""

    granted: bool
    reason: str = ""
    retry_after_ms: int = 0
    lease_id: str = ""


@dataclass(frozen=True)
class ScopeReservation:
    """The amount charged to one scope by one admitted call."""

    scope: QuotaScope
    counted_reserved: int


@dataclass
class ResourceLease:
    """A granted admission. Holds one concurrency slot per scope."""

    provider: str
    model: str
    operation: str
    lease_id: str
    admitted_at_ms: int
    scopes: tuple[ScopeReservation, ...]
    reserved_input_tokens: int
    reserved_output_tokens: int
    backend: Any = None
    reconciled: bool = False
    released: bool = False

    @staticmethod
    def counted_for(counting: TpmCounting, input_tokens: int, output_tokens: int) -> int:
        """Tokens a provider counts for the given real usage."""
        if counting is TpmCounting.INPUT:
            return max(0, input_tokens)
        if counting is TpmCounting.OUTPUT:
            return max(0, output_tokens)
        return max(0, input_tokens) + max(0, output_tokens)

    @property
    def counted_reserved(self) -> int:
        """The largest amount charged to any single scope by this lease."""
        return max((reservation.counted_reserved for reservation in self.scopes), default=0)


# ── Redis scripts ───────────────────────────────────────────────────────────
#
# Admission is one script so that a request, a token budget and a concurrency
# slot are granted or refused together. A partial grant (for example a token
# reservation without a slot) would leak budget, so the script verifies every
# scope before it consumes anything.

# KEYS: six keys per scope —
#   1 rpm sliding   2 rpm fixed base   3 tpm sliding
#   4 tpm sum       5 tpm fixed base   6 concurrency
# ARGV: 1 scope_count, 2 now_ms, 3 request_id, 4 lease_ttl_ms,
#       then eight values per scope:
#       max_concurrency, rpm_limit, rpm_window_ms, rpm_mode,
#       tpm_limit, tpm_window_ms, tpm_mode, tpm_charge
_ADMIT_LUA = """
local scope_count = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local request_id = ARGV[3]
local lease_ttl = tonumber(ARGV[4])

-- Pass 1: verify every scope before anything is consumed.
for s = 0, scope_count - 1 do
  local k = s * 6
  local a = 5 + (s * 8)
  local conc_key = KEYS[k + 6]
  local rpm_key = KEYS[k + 1]
  local rpm_fixed_base = KEYS[k + 2]
  local tpm_key = KEYS[k + 3]
  local tpm_sum_key = KEYS[k + 4]
  local tpm_fixed_base = KEYS[k + 5]

  local max_concurrency = tonumber(ARGV[a])
  local rpm_limit = tonumber(ARGV[a + 1])
  local rpm_window = tonumber(ARGV[a + 2])
  local rpm_mode = tonumber(ARGV[a + 3])
  local tpm_limit = tonumber(ARGV[a + 4])
  local tpm_window = tonumber(ARGV[a + 5])
  local tpm_mode = tonumber(ARGV[a + 6])
  local tpm_charge = tonumber(ARGV[a + 7])

  if max_concurrency > 0 then
    redis.call('ZREMRANGEBYSCORE', conc_key, '-inf', now)
    local in_flight = redis.call('ZCARD', conc_key)
    if in_flight >= max_concurrency then
      local oldest = redis.call('ZRANGE', conc_key, 0, 0, 'WITHSCORES')
      local retry = 1000
      if oldest[2] then
        retry = math.max(1, tonumber(oldest[2]) - now)
      end
      return {0, 'concurrency', retry, ''}
    end
  end

  if rpm_mode == 1 then
    redis.call('ZREMRANGEBYSCORE', rpm_key, '-inf', now - rpm_window)
    local used = redis.call('ZCARD', rpm_key)
    if used >= rpm_limit then
      local oldest = redis.call('ZRANGE', rpm_key, 0, 0, 'WITHSCORES')
      local retry = 1000
      if oldest[2] then
        retry = math.max(1, (tonumber(oldest[2]) + rpm_window) - now)
      end
      return {0, 'rpm', retry, ''}
    end
  elseif rpm_mode == 2 then
    local fixed_key = rpm_fixed_base .. ':' .. math.floor(now / rpm_window)
    local used = tonumber(redis.call('GET', fixed_key)) or 0
    if used >= rpm_limit then
      local ttl = redis.call('PTTL', fixed_key)
      return {0, 'rpm', math.max(1, ttl), ''}
    end
  end

  if tpm_mode == 1 then
    local expired = redis.call('ZRANGEBYSCORE', tpm_key, '-inf', now - tpm_window)
    if #expired > 0 then
      local released = 0
      for i = 1, #expired do
        local parts = string.match(expired[i], '^(.*):(%d+)$')
        if parts then
          released = released + tonumber(parts[2])
        end
      end
      redis.call('ZREMRANGEBYSCORE', tpm_key, '-inf', now - tpm_window)
      if released > 0 then
        local running = tonumber(redis.call('GET', tpm_sum_key)) or 0
        redis.call('SET', tpm_sum_key, math.max(0, math.floor(running - released)))
      end
    end
    local used = tonumber(redis.call('GET', tpm_sum_key)) or 0
    if used + tpm_charge > tpm_limit then
      local oldest = redis.call('ZRANGE', tpm_key, 0, 0, 'WITHSCORES')
      local retry = 1000
      if oldest[2] then
        retry = math.max(1, (tonumber(oldest[2]) + tpm_window) - now)
      end
      return {0, 'tpm', retry, ''}
    end
  elseif tpm_mode == 2 then
    local fixed_key = tpm_fixed_base .. ':' .. math.floor(now / tpm_window)
    local used = tonumber(redis.call('GET', fixed_key)) or 0
    if used + tpm_charge > tpm_limit then
      local ttl = redis.call('PTTL', fixed_key)
      return {0, 'tpm', math.max(1, ttl), ''}
    end
  end
end

-- Pass 2: consume every scope.
for s = 0, scope_count - 1 do
  local k = s * 6
  local a = 5 + (s * 8)
  local conc_key = KEYS[k + 6]
  local rpm_key = KEYS[k + 1]
  local rpm_fixed_base = KEYS[k + 2]
  local tpm_key = KEYS[k + 3]
  local tpm_sum_key = KEYS[k + 4]
  local tpm_fixed_base = KEYS[k + 5]

  local max_concurrency = tonumber(ARGV[a])
  local rpm_limit = tonumber(ARGV[a + 1])
  local rpm_window = tonumber(ARGV[a + 2])
  local rpm_mode = tonumber(ARGV[a + 3])
  local tpm_limit = tonumber(ARGV[a + 4])
  local tpm_window = tonumber(ARGV[a + 5])
  local tpm_mode = tonumber(ARGV[a + 6])
  local tpm_charge = tonumber(ARGV[a + 7])

  if rpm_mode == 1 then
    redis.call('ZADD', rpm_key, now, now .. ':' .. request_id)
    redis.call('PEXPIRE', rpm_key, rpm_window)
  elseif rpm_mode == 2 then
    local fixed_key = rpm_fixed_base .. ':' .. math.floor(now / rpm_window)
    redis.call('INCR', fixed_key)
    redis.call('PEXPIRE', fixed_key, rpm_window * 2)
  end

  if tpm_mode == 1 then
    local member = now .. ':' .. request_id .. ':' .. tpm_charge
    redis.call('ZADD', tpm_key, now, member)
    redis.call('PEXPIRE', tpm_key, tpm_window)
    local running = tonumber(redis.call('GET', tpm_sum_key)) or 0
    redis.call('SET', tpm_sum_key, math.floor(running + tpm_charge))
  elseif tpm_mode == 2 then
    local fixed_key = tpm_fixed_base .. ':' .. math.floor(now / tpm_window)
    redis.call('INCRBY', fixed_key, tpm_charge)
    redis.call('PEXPIRE', fixed_key, tpm_window * 2)
  end

  if max_concurrency > 0 then
    redis.call('ZADD', conc_key, now + lease_ttl, request_id)
    redis.call('PEXPIRE', conc_key, lease_ttl)
  end
end

return {1, 'granted', 0, request_id}
"""

# Reconcile one scope's reservation with the real provider usage.
# KEYS[1] tpm sliding   KEYS[2] tpm sum   KEYS[3] tpm fixed base
# ARGV: now_ms, member, reserved, actual, window_ms, fixed_index, mode
_RECONCILE_LUA = """
local now = tonumber(ARGV[1])
local member = ARGV[2]
local reserved = tonumber(ARGV[3])
local actual = tonumber(ARGV[4])
local window = tonumber(ARGV[5])
local fixed_index = ARGV[6]
local mode = tonumber(ARGV[7])

local tpm_key = KEYS[1]
local tpm_sum_key = KEYS[2]
local tpm_fixed_base = KEYS[3]

if mode == 2 then
  local current_index = math.floor(now / window)
  if current_index ~= tonumber(fixed_index) then
    -- A fixed window rolled over: refund the reservation from the bucket that
    -- was actually charged and charge the real usage to the current bucket.
    local old_key = tpm_fixed_base .. ':' .. fixed_index
    if reserved > 0 then
      redis.call('DECRBY', old_key, reserved)
    end
    if actual > 0 then
      local new_key = tpm_fixed_base .. ':' .. current_index
      redis.call('INCRBY', new_key, actual)
      redis.call('PEXPIRE', new_key, window * 2)
    end
    return 1
  end
  local key = tpm_fixed_base .. ':' .. current_index
  if actual > 0 then
    redis.call('INCRBY', key, actual)
  else
    redis.call('DECRBY', key, reserved)
  end
  redis.call('PEXPIRE', key, window * 2)
  return 1
end

if mode == 1 then
  local removed = redis.call('ZREM', tpm_key, member)
  local running = tonumber(redis.call('GET', tpm_sum_key)) or 0
  if removed == 1 then
    running = running - reserved
  end
  if actual > 0 then
    local new_member = now .. ':' .. member .. ':' .. actual
    redis.call('ZADD', tpm_key, now, new_member)
    redis.call('PEXPIRE', tpm_key, window)
    running = running + actual
  end
  if running < 0 then
    running = 0
  end
  redis.call('SET', tpm_sum_key, math.floor(running))
end

return 1
"""

# Release one concurrency lease.
# KEYS[1] concurrency   ARGV: lease_id
_RELEASE_LUA = """
local removed = redis.call('ZREM', KEYS[1], ARGV[1])
return removed
"""

# Renew one concurrency lease. An expired lease is never resurrected, so a
# worker that lost its slot cannot take it back after another worker reused it.
# KEYS[1] concurrency   ARGV: lease_id, now_ms, new_expiry_ms
_RENEW_LUA = """
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not score then
  return 0
end
if tonumber(score) <= tonumber(ARGV[2]) then
  return 0
end
redis.call('ZADD', KEYS[1], ARGV[3], ARGV[1])
redis.call('PEXPIRE', KEYS[1], math.max(1000, tonumber(ARGV[3]) - tonumber(ARGV[2])))
return 1
"""


# ── Backends ────────────────────────────────────────────────────────────────


class GovernorBackend:
    """Interface implemented by the shared and the in-process backends."""

    def admit(
        self,
        scopes: list[QuotaScope],
        reservations: list[int],
        *,
        request_id: str,
        now_ms: int,
    ) -> AdmissionOutcome:  # pragma: no cover - interface
        raise NotImplementedError

    def reconcile(
        self,
        scope: QuotaScope,
        lease: ResourceLease,
        reservation: ScopeReservation,
        counted_actual: int,
        now_ms: int,
    ) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def release(self, scope: QuotaScope, lease: ResourceLease) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def renew(self, scope: QuotaScope, lease: ResourceLease, now_ms: int) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class RedisGovernorBackend(GovernorBackend):
    """Shared budget across API and Worker instances.

    Every admission is one Lua script, so the request counters, the token
    budgets and the concurrency slots are granted together or not at all.
    """

    def __init__(self, client: Any, *, namespace: str = "gaobao:llm:v1") -> None:
        self.client = client
        self.namespace = namespace

    def _prefix(self, scope: QuotaScope) -> str:
        # The hash tag keeps every key of one scope in the same Redis Cluster
        # slot, so the multi-key Lua script stays valid on a clustered Redis.
        return f"{{{self.namespace}:{scope.scope_id}}}"

    def _keys(self, scope: QuotaScope) -> list[str]:
        prefix = self._prefix(scope)
        return [
            f"{prefix}:rpm",
            f"{prefix}:rpm:fixed",
            f"{prefix}:tpm",
            f"{prefix}:tpm:sum",
            f"{prefix}:tpm:fixed",
            f"{prefix}:conc",
        ]

    def admit(
        self,
        scopes: list[QuotaScope],
        reservations: list[int],
        *,
        request_id: str,
        now_ms: int,
    ) -> AdmissionOutcome:
        keys: list[str] = []
        args: list[Any] = [str(len(scopes)), str(now_ms), request_id, "0"]
        lease_ttl_ms = 0
        for scope, reservation in zip(scopes, reservations, strict=True):
            keys.extend(self._keys(scope))
            lease_ttl_ms = max(lease_ttl_ms, int(scope.lease_ttl_seconds * 1000))
            rpm_mode = 0 if scope.rpm <= 0 else (1 if scope.rpm_window is WindowMode.SLIDING else 2)
            tpm_mode = 0 if scope.tpm <= 0 else (1 if scope.tpm_window is WindowMode.SLIDING else 2)
            args.extend(
                [
                    str(int(scope.max_concurrency)),
                    str(int(scope.rpm)),
                    str(_WINDOW_MS),
                    str(rpm_mode),
                    str(int(scope.tpm)),
                    str(_WINDOW_MS),
                    str(tpm_mode),
                    str(int(reservation)),
                ]
            )
        args[3] = str(lease_ttl_ms)
        result = self.client.eval(_ADMIT_LUA, len(keys), *keys, *args)
        if not isinstance(result, (list, tuple)) or len(result) < 4:
            raise RuntimeError("Invalid governor admission response")
        return AdmissionOutcome(
            granted=int(result[0]) == 1,
            reason=str(result[1] or ""),
            retry_after_ms=int(result[2] or 0),
            lease_id=str(result[3] or ""),
        )

    def reconcile(
        self,
        scope: QuotaScope,
        lease: ResourceLease,
        reservation: ScopeReservation,
        counted_actual: int,
        now_ms: int,
    ) -> None:
        if scope.tpm <= 0:
            return
        mode = 1 if scope.tpm_window is WindowMode.SLIDING else 2
        member = f"{lease.admitted_at_ms}:{lease.lease_id}:{reservation.counted_reserved}"
        prefix = self._prefix(scope)
        self.client.eval(
            _RECONCILE_LUA,
            3,
            f"{prefix}:tpm",
            f"{prefix}:tpm:sum",
            f"{prefix}:tpm:fixed",
            str(now_ms),
            member,
            str(int(reservation.counted_reserved)),
            str(int(counted_actual)),
            str(_WINDOW_MS),
            str(lease.admitted_at_ms // _WINDOW_MS),
            str(mode),
        )

    def release(self, scope: QuotaScope, lease: ResourceLease) -> None:
        if scope.max_concurrency <= 0:
            return
        self.client.eval(_RELEASE_LUA, 1, f"{self._prefix(scope)}:conc", lease.lease_id)

    def renew(self, scope: QuotaScope, lease: ResourceLease, now_ms: int) -> bool:
        if scope.max_concurrency <= 0:
            return True
        expiry = now_ms + int(scope.lease_ttl_seconds * 1000)
        result = self.client.eval(
            _RENEW_LUA,
            1,
            f"{self._prefix(scope)}:conc",
            lease.lease_id,
            str(now_ms),
            str(expiry),
        )
        return int(result or 0) == 1


class LocalGovernorBackend(GovernorBackend):
    """Bounded in-process governor.

    Used when no shared Redis is configured, and as the explicit degraded
    mode when Redis is unreachable. It enforces the same limits for one
    process, so calls stay bounded, but the budget is not shared with other
    instances.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rpm: dict[str, deque[int]] = {}
        self._tpm: dict[str, deque[tuple[int, int]]] = {}
        self._leases: dict[str, dict[str, int]] = {}
        self._reservations: dict[str, tuple[int, int]] = {}

    def admit(
        self,
        scopes: list[QuotaScope],
        reservations: list[int],
        *,
        request_id: str,
        now_ms: int,
    ) -> AdmissionOutcome:
        with self._lock:
            for scope, reservation in zip(scopes, reservations, strict=True):
                leases = self._leases.setdefault(scope.scope_id, {})
                for key in [key for key, expiry in leases.items() if expiry <= now_ms]:
                    leases.pop(key, None)
                if scope.max_concurrency > 0 and len(leases) >= scope.max_concurrency:
                    oldest = min(leases.values()) if leases else now_ms
                    return AdmissionOutcome(False, "concurrency", max(1, oldest - now_ms))

                rpm_bucket = self._rpm.setdefault(scope.scope_id, deque())
                if scope.rpm > 0 and scope.rpm_window is WindowMode.SLIDING:
                    while rpm_bucket and rpm_bucket[0] <= now_ms - _WINDOW_MS:
                        rpm_bucket.popleft()
                    if len(rpm_bucket) >= scope.rpm:
                        return AdmissionOutcome(False, "rpm", max(1, rpm_bucket[0] + _WINDOW_MS - now_ms))
                elif scope.rpm > 0:
                    window_start = now_ms - (now_ms % _WINDOW_MS)
                    if rpm_bucket and rpm_bucket[0] == window_start:
                        rpm_bucket.clear()
                    if len(rpm_bucket) >= scope.rpm:
                        return AdmissionOutcome(False, "rpm", _WINDOW_MS)

                tpm_bucket = self._tpm.setdefault(scope.scope_id, deque())
                if scope.tpm > 0:
                    while tpm_bucket and tpm_bucket[0][0] <= now_ms - _WINDOW_MS:
                        tpm_bucket.popleft()
                    used = sum(tokens for _, tokens in tpm_bucket)
                    if used + reservation > scope.tpm:
                        return AdmissionOutcome(False, "tpm", _WINDOW_MS)

            for scope, reservation in zip(scopes, reservations, strict=True):
                if scope.rpm > 0:
                    self._rpm.setdefault(scope.scope_id, deque()).append(now_ms)
                if scope.tpm > 0:
                    self._tpm.setdefault(scope.scope_id, deque()).append((now_ms, reservation))
                    self._reservations[f"{scope.scope_id}:{request_id}"] = (now_ms, reservation)
                if scope.max_concurrency > 0:
                    self._leases.setdefault(scope.scope_id, {})[request_id] = now_ms + int(
                        scope.lease_ttl_seconds * 1000
                    )
        return AdmissionOutcome(True, "granted", 0, request_id)

    def reconcile(
        self,
        scope: QuotaScope,
        lease: ResourceLease,
        reservation: ScopeReservation,
        counted_actual: int,
        now_ms: int,
    ) -> None:
        if scope.tpm <= 0:
            return
        with self._lock:
            bucket = self._tpm.get(scope.scope_id)
            if not bucket:
                return
            key = f"{scope.scope_id}:{lease.lease_id}"
            stored = self._reservations.pop(key, None)
            if stored is None:
                if counted_actual:
                    bucket.append((now_ms, counted_actual))
                return
            admitted_at, reserved = stored
            for index, (timestamp, tokens) in enumerate(bucket):
                if timestamp == admitted_at and tokens == reserved:
                    bucket[index] = (now_ms, counted_actual)
                    return
            if counted_actual:
                bucket.append((now_ms, counted_actual))

    def release(self, scope: QuotaScope, lease: ResourceLease) -> None:
        if scope.max_concurrency <= 0:
            return
        with self._lock:
            self._leases.get(scope.scope_id, {}).pop(lease.lease_id, None)
            self._reservations.pop(f"{scope.scope_id}:{lease.lease_id}", None)

    def renew(self, scope: QuotaScope, lease: ResourceLease, now_ms: int) -> bool:
        if scope.max_concurrency <= 0:
            return True
        with self._lock:
            leases = self._leases.get(scope.scope_id, {})
            expiry = leases.get(lease.lease_id)
            if expiry is None or expiry <= now_ms:
                return False
            leases[lease.lease_id] = now_ms + int(scope.lease_ttl_seconds * 1000)
            return True


# ── Configuration loading ──────────────────────────────────────────────────


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _quota_from_mapping(raw: dict[str, Any]) -> ProviderQuota:
    fallback_raw = raw.get("fallback")
    if isinstance(fallback_raw, str):
        fallback = tuple(part.strip() for part in fallback_raw.split(",") if part.strip())
    elif isinstance(fallback_raw, (list, tuple)):
        fallback = tuple(str(part).strip() for part in fallback_raw if str(part).strip())
    else:
        fallback = ()
    return ProviderQuota(
        rpm=_as_int(raw.get("rpm"), 0),
        tpm=_as_int(raw.get("tpm"), 0),
        max_concurrency=_as_int(raw.get("max_concurrency"), 0),
        rpm_window=WindowMode(str(raw.get("rpm_window", WindowMode.SLIDING.value))),
        tpm_window=WindowMode(str(raw.get("tpm_window", WindowMode.SLIDING.value))),
        tpm_counting=TpmCounting(str(raw.get("tpm_counting", TpmCounting.TOTAL.value))),
        fallback=fallback,
        pool=(str(raw["pool"]).strip() or None) if raw.get("pool") else None,
        lease_ttl_seconds=(
            _as_float(raw.get("lease_ttl_seconds"), 0.0) if raw.get("lease_ttl_seconds") is not None else None
        ),
    )


def load_governance_config(path: str | None = None) -> GovernanceConfig:
    """Load governance configuration from YAML plus environment overrides.

    The YAML file carries provider, pool and model entries; process wide
    switches come from the environment so a deployment can change the failure
    policy without editing the file.
    """
    from config.loader import load_yaml_mapping

    config_path = path or os.getenv("GAOBAO_LLM_GOVERNANCE_CONFIG", "").strip()
    if not config_path:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config",
            "llm_governance.yaml",
        )
    raw: dict[str, Any] = {}
    if config_path:
        raw = load_yaml_mapping(config_path)
    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        defaults = {}

    failure_mode = os.getenv("GAOBAO_LLM_GOVERNOR_REDIS_FAILURE_MODE", "").strip().lower()
    if not failure_mode:
        failure_mode = str(defaults.get("redis_failure_mode", RedisFailureMode.FAIL_CLOSED.value)).strip().lower()
    try:
        redis_failure_mode = RedisFailureMode(failure_mode)
    except ValueError:
        redis_failure_mode = RedisFailureMode.FAIL_CLOSED

    enabled = _as_bool(os.getenv("GAOBAO_LLM_GOVERNOR_ENABLED"), _as_bool(defaults.get("enabled"), True))

    providers = {
        str(name).strip(): _quota_from_mapping(value)
        for name, value in (raw.get("providers") or {}).items()
        if isinstance(value, dict)
    }
    pools = {
        str(name).strip(): _quota_from_mapping(value)
        for name, value in (raw.get("pools") or {}).items()
        if isinstance(value, dict)
    }
    models = {
        str(name).strip(): _quota_from_mapping(value)
        for name, value in (raw.get("models") or {}).items()
        if isinstance(value, dict)
    }

    return GovernanceConfig(
        enabled=enabled,
        namespace=os.getenv("GAOBAO_LLM_GOVERNOR_NAMESPACE", "").strip()
        or str(defaults.get("namespace", "gaobao:llm:v1")).strip()
        or "gaobao:llm:v1",
        redis_failure_mode=redis_failure_mode,
        max_wait_seconds=_as_float(
            os.getenv("GAOBAO_LLM_GOVERNOR_MAX_WAIT_SECONDS"),
            _as_float(defaults.get("max_wait_seconds"), 20.0),
        ),
        default_lease_ttl_seconds=_as_float(
            os.getenv("GAOBAO_LLM_GOVERNOR_LEASE_TTL_SECONDS"),
            _as_float(defaults.get("lease_ttl_seconds"), 120.0),
        ),
        default_output_reservation=_as_int(
            os.getenv("GAOBAO_LLM_GOVERNOR_OUTPUT_RESERVATION"),
            _as_int(defaults.get("output_reservation"), 512),
        ),
        default_rpm=_as_int(defaults.get("rpm"), 0),
        default_tpm=_as_int(defaults.get("tpm"), 0),
        default_max_concurrency=_as_int(defaults.get("max_concurrency"), 0),
        providers=providers,
        pools=pools,
        models=models,
    )


def _merge_quota(base: ProviderQuota, override: ProviderQuota) -> ProviderQuota:
    """Provider entries refine a pool entry without erasing pool limits."""
    return ProviderQuota(
        rpm=override.rpm or base.rpm,
        tpm=override.tpm or base.tpm,
        max_concurrency=override.max_concurrency or base.max_concurrency,
        rpm_window=override.rpm_window,
        tpm_window=override.tpm_window,
        tpm_counting=override.tpm_counting,
        fallback=override.fallback or base.fallback,
        pool=override.pool or base.pool,
        lease_ttl_seconds=override.lease_ttl_seconds or base.lease_ttl_seconds,
    )


# ── Governor ────────────────────────────────────────────────────────────────


class LLMResourceGovernor:
    """Resolves quota scopes and grants or refuses one model call."""

    def __init__(self, config: GovernanceConfig | None = None, backend: GovernorBackend | None = None) -> None:
        self.config = config or load_governance_config()
        self._backend = backend
        self._redis_client: Any = None
        self._lock = threading.Lock()

    # -- backend selection ------------------------------------------------
    @property
    def backend(self) -> GovernorBackend:
        if self._backend is not None:
            return self._backend
        with self._lock:
            if self._backend is None:
                client = self._redis_client
                if client is None:
                    client = self._connect_redis()
                    self._redis_client = client
                self._backend = (
                    RedisGovernorBackend(client, namespace=self.config.namespace)
                    if client is not None
                    else LocalGovernorBackend()
                )
        return self._backend

    def _connect_redis(self) -> Any | None:
        url = os.getenv("REDIS_URL", "").strip()
        if not url:
            return None
        try:
            import redis
        except ImportError:  # pragma: no cover - optional dependency
            logger.warning("redis package is not installed; using the bounded local governor")
            return None
        try:
            client = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=2,
            )
            client.ping()
        except Exception as exc:
            logger.warning("Redis governor backend unavailable (%s)", type(exc).__name__)
            return None
        return client

    # -- quota resolution --------------------------------------------------
    def _scope(
        self,
        scope_id: str,
        quota: ProviderQuota,
        lease_ttl_seconds: float | None,
        *,
        use_defaults: bool,
    ) -> QuotaScope:
        rpm = quota.rpm or (self.config.default_rpm if use_defaults else 0)
        tpm = quota.tpm or (self.config.default_tpm if use_defaults else 0)
        concurrency = quota.max_concurrency or (self.config.default_max_concurrency if use_defaults else 0)
        return QuotaScope(
            scope_id=scope_id,
            rpm=rpm,
            tpm=tpm,
            max_concurrency=concurrency,
            rpm_window=quota.rpm_window,
            tpm_window=quota.tpm_window,
            tpm_counting=quota.tpm_counting,
            lease_ttl_seconds=max(1.0, lease_ttl_seconds or self.config.default_lease_ttl_seconds),
        )

    def resolve_scopes(self, provider: str, model: str | None = None) -> list[QuotaScope]:
        """Resolve every budget a call must satisfy.

        The account scope comes from the provider (or the shared pool the
        provider belongs to). A model that declares its own budget adds a
        second scope that is charged in the same atomic admission.
        """
        provider_quota = self.config.providers.get(provider)
        pool_quota = None
        if provider_quota is not None and provider_quota.pool:
            pool_quota = self.config.pools.get(provider_quota.pool)

        if pool_quota is not None:
            account_scope_id = f"pool:{provider_quota.pool}"
            account_quota = _merge_quota(pool_quota, provider_quota)
        elif provider_quota is not None:
            account_scope_id = f"provider:{provider}"
            account_quota = provider_quota
        else:
            account_scope_id = f"provider:{provider}"
            account_quota = ProviderQuota()

        scopes = [
            self._scope(
                account_scope_id,
                account_quota,
                (provider_quota.lease_ttl_seconds if provider_quota else None),
                use_defaults=True,
            )
        ]

        model_quota = self.config.models.get(model) if model else None
        if model_quota is not None and (model_quota.rpm or model_quota.tpm or model_quota.max_concurrency):
            if model_quota.pool:
                model_pool = self.config.pools.get(model_quota.pool)
                merged = _merge_quota(model_pool, model_quota) if model_pool else model_quota
                model_scope_id = f"pool:{model_quota.pool}"
            else:
                merged = model_quota
                model_scope_id = f"model:{model}"
            if model_scope_id != account_scope_id:
                scopes.append(
                    self._scope(
                        model_scope_id,
                        merged,
                        model_quota.lease_ttl_seconds,
                        use_defaults=False,
                    )
                )
        return scopes

    def fallback_chain(self, provider: str) -> tuple[str, ...]:
        """Providers to try after *provider* is exhausted or rate limited."""
        quota = self.config.providers.get(provider)
        chain = quota.fallback if quota else ()
        if not chain:
            from server.services.llm_router import LLMRouter

            chain = tuple(LLMRouter().build_fallback_chain(provider))
        ordered: list[str] = []
        for candidate in chain:
            name = str(candidate).strip()
            if name and name not in ordered:
                ordered.append(name)
        return tuple(ordered)

    # -- admission ---------------------------------------------------------
    def acquire(
        self,
        provider: str,
        *,
        model: str = "",
        operation: str = "chat",
        estimated_input_tokens: int = 0,
        reserved_output_tokens: int = 0,
        request_id: str | None = None,
    ) -> ResourceLease:
        """Grant one model call, or raise :class:`LLMQuotaExceeded`."""
        scopes = self.resolve_scopes(provider, model)
        call_id = request_id or uuid.uuid4().hex
        now_ms = int(time.time() * 1000)
        reservations = [
            ResourceLease.counted_for(scope.tpm_counting, estimated_input_tokens, reserved_output_tokens)
            for scope in scopes
        ]

        if not self.config.enabled:
            return self._lease(
                scopes,
                reservations,
                provider,
                model,
                operation,
                estimated_input_tokens,
                reserved_output_tokens,
                call_id,
                now_ms=now_ms,
            )

        backend = self.backend
        try:
            outcome = backend.admit(scopes, reservations, request_id=call_id, now_ms=now_ms)
        except Exception as exc:
            outcome = self._handle_backend_failure(scopes, reservations, call_id, now_ms, exc)

        if not outcome.granted:
            raise LLMQuotaExceeded(
                outcome.reason or "rejected",
                provider=provider,
                retry_after=outcome.retry_after_ms / 1000.0,
            )
        lease = self._lease(
            scopes,
            reservations,
            provider,
            model,
            operation,
            estimated_input_tokens,
            reserved_output_tokens,
            call_id,
            now_ms=now_ms,
            backend=backend,
        )
        try:
            from server.metrics import adjust_governor_concurrency

            adjust_governor_concurrency(provider, 1)
        except Exception:  # pragma: no cover - metrics must never break a call
            pass
        return lease

    def _handle_backend_failure(
        self,
        scopes: list[QuotaScope],
        reservations: list[int],
        call_id: str,
        now_ms: int,
        exc: Exception,
    ) -> AdmissionOutcome:
        """Apply the configured Redis failure policy. Never allow unbounded calls."""
        if self.config.redis_failure_mode is RedisFailureMode.LOCAL_FALLBACK:
            logger.warning("Redis governor backend failed (%s); using the bounded local governor", type(exc).__name__)
            fallback = LocalGovernorBackend()
            self._backend = fallback
            return fallback.admit(scopes, reservations, request_id=call_id, now_ms=now_ms)
        logger.error("Redis governor backend failed (%s); refusing the model call", type(exc).__name__)
        raise LLMQuotaExceeded("redis_unavailable", provider=scopes[0].scope_id, retry_after=1.0) from exc

    def _lease(
        self,
        scopes: list[QuotaScope],
        reservations: list[int],
        provider: str,
        model: str,
        operation: str,
        input_tokens: int,
        output_tokens: int,
        request_id: str,
        *,
        now_ms: int | None = None,
        backend: GovernorBackend | None = None,
    ) -> ResourceLease:
        return ResourceLease(
            provider=provider,
            model=model,
            operation=operation,
            lease_id=request_id,
            admitted_at_ms=now_ms if now_ms is not None else int(time.time() * 1000),
            scopes=tuple(
                ScopeReservation(scope=scope, counted_reserved=reservation)
                for scope, reservation in zip(scopes, reservations, strict=True)
            ),
            reserved_input_tokens=max(0, input_tokens),
            reserved_output_tokens=max(0, output_tokens),
            backend=backend or self.backend,
        )

    # -- settlement --------------------------------------------------------
    def reconcile(self, lease: ResourceLease, *, input_tokens: int = 0, output_tokens: int = 0) -> int:
        """Replace the reservation with the real provider usage."""
        if lease.reconciled or lease.released:
            return 0
        lease.reconciled = True
        counted = 0
        for reservation in lease.scopes:
            actual = ResourceLease.counted_for(
                reservation.scope.tpm_counting,
                input_tokens,
                output_tokens,
            )
            counted = max(counted, actual)
            try:
                lease.backend.reconcile(
                    reservation.scope,
                    lease,
                    reservation,
                    actual,
                    int(time.time() * 1000),
                )
            except Exception as exc:
                logger.warning("Governor reconciliation failed (type=%s)", type(exc).__name__)
        return counted

    def release(self, lease: ResourceLease) -> None:
        """Return the concurrency slots. Safe to call more than once."""
        if lease.released:
            return
        lease.released = True
        for reservation in lease.scopes:
            try:
                lease.backend.release(reservation.scope, lease)
            except Exception as exc:
                logger.warning("Governor release failed (type=%s)", type(exc).__name__)
        try:
            from server.metrics import adjust_governor_concurrency

            adjust_governor_concurrency(lease.provider, -1)
        except Exception:  # pragma: no cover - metrics must never break a call
            pass

    def renew(self, lease: ResourceLease) -> bool:
        """Extend the concurrency leases. An expired lease is never resurrected."""
        if lease.released:
            return False
        renewed = True
        for reservation in lease.scopes:
            try:
                renewed = lease.backend.renew(reservation.scope, lease, int(time.time() * 1000)) and renewed
            except Exception as exc:
                logger.warning("Governor lease renewal failed (type=%s)", type(exc).__name__)
                renewed = False
        return renewed


# ── Process wide singleton ─────────────────────────────────────────────────

_governor: LLMResourceGovernor | None = None
_governor_lock = threading.Lock()


def get_governor() -> LLMResourceGovernor:
    """Return the process wide governor."""
    global _governor
    if _governor is None:
        with _governor_lock:
            if _governor is None:
                _governor = LLMResourceGovernor()
    return _governor


def reset_governor() -> None:
    """Drop the cached governor. Used by tests and by configuration reloads."""
    global _governor
    with _governor_lock:
        _governor = None


"""Per-session and per-IP throttling for the chat endpoints.

Every /chat call fans out to Azure OpenAI (rewrite + answer + guardrail judges), so an
unthrottled endpoint is a direct cost exposure to anyone who has the URL. Two limits stack:

  session_id  the real fairness limit. The widget mints a UUID per browser tab, so this is
              roughly "one student", and it is tight enough to kill a runaway loop or one
              abusive tab. It is client-supplied and therefore spoofable, which is why the
              IP limit exists underneath it.
  client IP   anti-scripting backstop only. Students may sit behind a campus NAT and share
              one public IP, so this is deliberately loose - it should never trip for a
              classroom, only for someone hammering the endpoint from one machine.

Storage is in-process, same as the session history dict in chain.py, so it holds only
because the app runs a single uvicorn worker. Counters reset on restart/redeploy.
"""

import time
from dataclasses import dataclass

from fastapi import Request
from limits import RateLimitItem, parse
from limits.aio.storage import MemoryStorage
from limits.aio.strategies import MovingWindowRateLimiter

# Message the student sees when they trip a limit. Plain HTML like the guardrail responses,
# because the widget renders whatever comes back as a normal bot message.
RATE_LIMIT_RESPONSE = (
    "<p>You're sending messages faster than I can keep up with. Give it a moment and try again.</p>"
)

# Reason codes for the logs, matching the BLOCK_* convention in logger.py
BLOCK_RATE_LIMIT_SESSION = "rate_limit_session"
BLOCK_RATE_LIMIT_IP = "rate_limit_ip"

# A human sends maybe one message every 10-30s; 15/min leaves plenty of headroom while
# still stopping a script pointed at one session_id.
SESSION_LIMITS: list[RateLimitItem] = [parse("15/minute"), parse("100/hour")]

# Loose enough that a NAT'd campus never trips it - this only catches single-machine abuse.
IP_LIMITS: list[RateLimitItem] = [parse("300/minute")]

_storage = MemoryStorage()
_limiter = MovingWindowRateLimiter(_storage)


@dataclass
class RateLimitBlock:
    """Returned when a limit trips; None means the request may proceed."""

    reason: str  # one of the BLOCK_RATE_LIMIT_* codes
    retry_after: int  # seconds, for the Retry-After header


def client_ip(request: Request) -> str:
    """Real client IP, not Azure's front end.

    App Service terminates the connection at its load balancer, so request.client.host is
    an internal address for every caller. The originating IP is the first entry of
    X-Forwarded-For, which Azure writes as "ip:port".
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        # Strip the port Azure appends. rsplit keeps bare IPv6 addresses intact.
        if first.count(":") == 1:
            first = first.rsplit(":", 1)[0]
        if first:
            return first
    return request.client.host if request.client else "unknown"


async def _first_exceeded(limits: list[RateLimitItem], *identifiers: str) -> RateLimitItem | None:
    """Consume one unit against each limit, returning the first that is now exhausted.

    hit() both tests and records, so a request that trips the first limit is not counted
    against the rest - that only matters for which window's retry_after we report.
    """
    for limit in limits:
        if not await _limiter.hit(limit, *identifiers):
            return limit
    return None


async def _retry_after(limit: RateLimitItem, *identifiers: str) -> int:
    reset_at, _remaining = await _limiter.get_window_stats(limit, *identifiers)
    # get_window_stats returns an absolute epoch reset time; never advertise less than 1s
    return max(1, int(reset_at - time.time()))


async def check_rate_limit(request: Request, session_id: str) -> RateLimitBlock | None:
    """Charge this request against both limits. Returns a block, or None to proceed."""
    # Session first: it is the tighter limit, so it is the one a real student would ever see.
    exceeded = await _first_exceeded(SESSION_LIMITS, "session", session_id)
    if exceeded is not None:
        return RateLimitBlock(
            reason=BLOCK_RATE_LIMIT_SESSION,
            retry_after=await _retry_after(exceeded, "session", session_id),
        )

    ip = client_ip(request)
    exceeded = await _first_exceeded(IP_LIMITS, "ip", ip)
    if exceeded is not None:
        return RateLimitBlock(
            reason=BLOCK_RATE_LIMIT_IP,
            retry_after=await _retry_after(exceeded, "ip", ip),
        )

    return None


async def reset_rate_limits() -> None:
    """Drop all counters. Used by tests so one test's requests don't throttle the next."""
    await _storage.reset()

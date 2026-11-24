"""Security utilities: injection detection, SSRF defense, XSS sanitization."""

import ipaddress
import logging
import re
from urllib.parse import urlparse

INPUT_MAX_LENGTH = 3000

# --- Prompt Injection Detection ---
# 17 original patterns migrated from agent.py _INJECTION_PATTERNS (lines 770-788)
# Plus supplementary patterns to cover the full attack surface
_INJECTION_PATTERNS = [
    r"(?i)ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|prompts|rules)",
    r"(?i)ignore\s+previous\s+and\s+",
    r"(?i)forget\s+(?:all\s+)?(?:previous|prior|above)",
    r"(?i)you\s+are\s+now\s+(?:a|an|the)",
    r"(?i)new\s+(?:system\s+)?(?:instructions?|prompt|rules?|role)",
    r"(?i)override\s+(?:your|the|safety)\s+(?:guidelines?|instructions?|rules?|system)",
    r"(?i)override\s+your\s+safety",
    r"(?i)output\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?|rules?)",
    r"(?i)reveal\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)",
    r"(?i)repeat\s+(?:your|the)\s+(?:initial\s+|system\s+)?(?:prompt|instructions?)",
    r"(?i)print\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)",
    r"(?i)show\s+me\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)",
    r"(?i)what\s+(?:are|is)\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)",
    r"(?i)\bDAN\b.*\bjailbreak\b",
    r"(?i)pretend\s+you\s+(?:are|have)",
    r"(?i)act\s+as\s+(?:if|though)",
    r"(?i)disregard\s+(?:all|any|the)",
    r"(?i)from\s+now\s+on\s+(?:you|respond|answer|output)",
    r"(?i)system:\s*(?:you|ignore|forget|new)",
]

# Chinese-language injection patterns (from gaobao-advisor specific needs)
_CN_INJECTION_PATTERNS = [
    re.compile(r"忽略.{0,10}(之前|上面|以前|过去).{0,10}(指令|提示|规则)"),
    re.compile(r"(你不再|you are not).{0,15}(高考|顾问|assistant)", re.IGNORECASE),
    re.compile(r"```system"),
    re.compile(r"从现在开始.{0,10}假装"),
    re.compile(r"假装.{0,5}(你)?(没有|不受).{0,5}(限制|约束)"),
    re.compile(r"(真实身份|real identity|system prompt|系统提示)"),
    re.compile(r"[Pp]lease\s+[Ii]gnore"),
]

_INJECTION_RE = [re.compile(p) for p in _INJECTION_PATTERNS]


def detect_injection(text: str) -> bool:
    """Return True if input contains prompt injection patterns."""
    # Length check first — use the canonical threshold
    if len(text) > INPUT_MAX_LENGTH:
        return True
    # English patterns
    for pattern in _INJECTION_RE:
        if pattern.search(text):
            return True
    # Chinese-specific patterns
    for pattern in _CN_INJECTION_PATTERNS:
        if pattern.search(text):
            return True
    return False


# --- Input Sanitization ---
_TAG_RE = re.compile(r"<[^<>]*>")


def sanitize_input(text: str) -> str:
    """Strip XSS vectors and enforce length limit."""
    text = _TAG_RE.sub("", text)
    if len(text) > INPUT_MAX_LENGTH:
        text = text[:INPUT_MAX_LENGTH]
    return text.strip()


# --- SSRF Defense ---
_BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "169.254.169.254",
    "metadata.google.internal",
    "100.100.100.200",
}

_PRIVATE_RANGES = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
]


def _parse_ip(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Parse an IP address, including hex (0x7f000001) and decimal (2130706433) forms."""
    # Try standard format first
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        pass
    # Try parsing as hex (e.g. 0x7f000001)
    try:
        return ipaddress.ip_address(int(hostname, 16))
    except (ValueError, TypeError):
        pass
    # Try parsing as decimal integer (e.g. 2130706433)
    try:
        return ipaddress.ip_address(int(hostname, 10))
    except (ValueError, TypeError):
        pass
    raise ValueError(f"Not a valid IP: {hostname}")


def check_ssrf(url: str) -> bool:
    """Return True if URL points to a private/internal address (should block)."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return True

        # Check explicit blocked hostnames
        hostname_lower = hostname.lower()
        if hostname_lower in _BLOCKED_HOSTS:
            return True

        # Check for internal domain suffixes
        for suffix in (".local", ".internal", ".localhost", ".lan"):
            if hostname_lower.endswith(suffix):
                return True

        # Check if hostname is an IP address (including hex/decimal forms)
        try:
            ip = _parse_ip(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
            return False
        except ValueError:
            # Not an IP address; hostname is a domain name — safe
            return False
    except ValueError:
        # Malformed URL — let it fail elsewhere, don't silently block
        return False
    except Exception:
        return True  # other errors = block for safety


# --- FastAPI Middleware ---
from fastapi import Request  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402


class SecurityMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for injection detection on chat endpoints."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/v1/chat":
            try:
                body = await request.json()
                message = body.get("message", "")
                if detect_injection(message):
                    from starlette.responses import JSONResponse

                    return JSONResponse(status_code=400, content={"detail": "输入内容包含不允许的指令"})
                # Sanitize: strip HTML tags and enforce length limit
                sanitized = sanitize_input(message)
                body["message"] = sanitized
                # Re-encode the modified body so downstream handlers see sanitized input
                import json as _json

                raw_body = _json.dumps(body).encode("utf-8")
                request._body = raw_body
            except Exception as exc:
                logging.debug("Security middleware body processing failed: %s", exc, exc_info=True)
        response = await call_next(request)
        return response

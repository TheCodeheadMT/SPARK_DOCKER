"""LLM provider layer for EI.

The API key lives on the server only (OPENAI_API_KEY); the browser never sees it.
Providers:
  * openai  | OpenAI or any OpenAI-compatible endpoint (OPENAI_BASE_URL), e.g. Azure OpenAI
  * fake    | deterministic text for tests and demos, no network
  * none    | generation disabled; prompt preview still works

Every call is rate limited per user (LLM_RATE_PER_HOUR). The limiter is in memory,
which is right for one API process; with several workers it becomes per worker and
should move to Redis with the rest of Phase 5.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass

log = logging.getLogger("spark.llm")


class LLMUnavailable(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, retry_after_s: int):
        super().__init__(f"Rate limit reached. Try again in about {retry_after_s // 60 + 1} minutes.")
        self.retry_after_s = retry_after_s


@dataclass
class LLMRequest:
    system: str
    user: str
    model: str
    temperature: float


class Provider:
    name = "none"

    def stream(self, req: LLMRequest) -> Iterator[str]:
        raise LLMUnavailable("AI generation is not configured on this server (set LLM_PROVIDER and OPENAI_API_KEY).")


class FakeProvider(Provider):
    name = "fake"

    def stream(self, req: LLMRequest) -> Iterator[str]:
        first = req.user.strip().splitlines()[0] if req.user.strip() else ""
        text = ("## Diagnostic summary\n\nThis is a **simulated** response generated without an AI service.\n\n"
                f"- Model: `{req.model}`\n- Temperature: {req.temperature:.2f}\n- Prompt starts: {first[:80]}\n\n"
                "## Practice\n\n1. Explain the key idea in your own words.\n2. Work one example without notes.\n\n"
                "- [ ] Review your notes for 10 minutes\n")
        for i in range(0, len(text), 24):
            yield text[i:i + 24]


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, api_key: str, base_url: str = "", timeout_s: float = 120.0):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout_s)

    def _create(self, req: LLMRequest, with_temperature: bool):
        kw = dict(model=req.model, stream=True,
                  messages=[{"role": "system", "content": req.system}, {"role": "user", "content": req.user}])
        if with_temperature:
            kw["temperature"] = req.temperature
        return self.client.chat.completions.create(**kw)

    def stream(self, req: LLMRequest) -> Iterator[str]:
        from openai import BadRequestError
        try:
            resp = self._create(req, True)
        except BadRequestError as exc:
            # Reasoning models (gpt-5 family) reject a custom temperature; retry without it.
            if "temperature" not in str(exc).lower():
                raise
            log.info("model %s rejected temperature; retrying without it", req.model)
            resp = self._create(req, False)
        for chunk in resp:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class RateLimiter:
    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        if self.per_hour <= 0:
            return
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= self.per_hour:
                raise RateLimited(int(3600 - (now - q[0])))
            q.append(now)


class LLMService:
    def __init__(self, provider: Provider, models: list[str], default_model: str, per_hour: int,
                 disabled_reason: str | None = None):
        self.provider = provider
        self.disabled_reason = disabled_reason
        self.models = models
        self.default_model = default_model if default_model in models else (models[0] if models else "")
        self.limiter = RateLimiter(per_hour)

    @property
    def enabled(self) -> bool:
        return self.provider.name != "none"

    def info(self) -> dict:
        return {"enabled": self.enabled, "provider": self.provider.name, "disabled_reason": self.disabled_reason,
                "models": self.models,
                "default_model": self.default_model, "rate_per_hour": self.limiter.per_hour}

    def stream(self, user_key: str, system: str, user: str, model: str | None, temperature: float) -> Iterator[str]:
        if not self.enabled:
            raise LLMUnavailable("AI generation is not configured on this server.")
        model = model if model in self.models else self.default_model
        self.limiter.check(user_key)
        # Same cap as the original app: creativity never above 0.6.
        req = LLMRequest(system=system, user=user, model=model, temperature=max(0.0, min(float(temperature), 0.6)))
        return self.provider.stream(req)

    def complete(self, user_key: str, system: str, user: str, model: str | None, temperature: float) -> str:
        return "".join(self.stream(user_key, system, user, model, temperature))


def describe_error(exc: BaseException, model: str | None = None) -> str:
    """Plain-language explanation of an AI service error, for the instructor and the admin.

    Uses only the error type, status and code from OpenAI (plus OpenAI's own message), never
    anything from the prompt.
    """
    try:
        import openai
    except ImportError:  # pragma: no cover
        return f"AI service error: {type(exc).__name__}"
    code = (getattr(exc, "code", None) or getattr(exc, "type", None) or "")
    raw = getattr(exc, "body", None)
    detail = raw.get("message") if isinstance(raw, dict) else None
    detail = f' OpenAI said: "{detail[:300]}"' if detail else ""
    m = f" ({model})" if model else ""
    if isinstance(exc, openai.RateLimitError):
        if code == "insufficient_quota":
            return ("The OpenAI account has no usable quota: its credit or budget is used up, billing isn't set up, "
                    "or the project has a spending limit of $0. This is set in the OpenAI account (for a university "
                    "account, ask whoever manages it), not in SPARK." + detail)
        return ("OpenAI's rate limit for this account was reached (too many requests or tokens per minute). "
                "Wait a minute and try again; if it keeps happening, the account's usage tier is low." + detail)
    if isinstance(exc, openai.AuthenticationError):
        return "OpenAI rejected the API key (wrong, revoked or expired). Check OPENAI_API_KEY in .env." + detail
    if isinstance(exc, (openai.PermissionDeniedError, openai.NotFoundError)):
        return (f"This API key isn't allowed to use the selected model{m}. Choose another model, or ask the "
                "account manager to enable it for the project." + detail)
    if isinstance(exc, openai.BadRequestError):
        return f"OpenAI refused the request{m}." + detail
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return ("Couldn't reach OpenAI (network problem or timeout). Check the server's internet access, or "
                "OPENAI_BASE_URL if you use a gateway.")
    if isinstance(exc, openai.InternalServerError):
        return "OpenAI had a server problem. Try again in a few minutes." + detail
    return f"AI service error: {type(exc).__name__}" + detail


def build_llm(settings) -> LLMService:
    p = settings.llm_provider
    where = (f"in {settings.env_file}" if getattr(settings, "env_file", None)
             else "(no .env file was found in the project folder, next to README.md)")
    reason = None
    if p == "openai" and settings.openai_api_key:
        provider: Provider = OpenAIProvider(settings.openai_api_key, settings.openai_base_url)
    elif p == "fake":
        provider = FakeProvider()
    else:
        provider = Provider()
        if p == "openai":
            reason = (f"LLM_PROVIDER is openai but OPENAI_API_KEY is empty {where}. "
                      "Check the key line has no # in front of it.")
        else:
            reason = f"LLM_PROVIDER is '{p}' {where}. Set LLM_PROVIDER=openai and OPENAI_API_KEY."
        log.warning("AI generation disabled: %s", reason)
    if reason is None:
        logging.getLogger("uvicorn.error").info("SPARK AI generation: %s (default model %s)", provider.name,
                                                settings.llm_default_model)
    return LLMService(provider, settings.llm_models, settings.llm_default_model, settings.llm_rate_per_hour, reason)

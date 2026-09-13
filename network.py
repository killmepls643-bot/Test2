"""
Resilient Network Engine featuring AIMD Semaphore Limiters, Proxy Pools, and Fallbacks.
"""

import asyncio
import random
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp
import structlog
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from config import settings

logger = structlog.get_logger(__name__)


class AdaptiveSemaphore:
    """Additive Increase / Multiplicative Decrease (AIMD) host rate governor."""
    def __init__(self, initial_limit: int, min_limit: int = 2, max_limit: int = 25):
        self.current_limit = float(initial_limit)
        self.min_limit = min_limit
        self.max_limit = max_limit
        self.semaphore = asyncio.Semaphore(initial_limit)
        self._lock = asyncio.Lock()

    async def acquire(self):
        await self.semaphore.acquire()

    def release(self):
        self.semaphore.release()

    async def on_success(self):
        async with self._lock:
            if self.current_limit < self.max_limit:
                self.current_limit = min(self.max_limit, self.current_limit + settings.AIMD_INCREASE_STEP)
                # Softly adjust pool capacity
                if self.semaphore._value < int(self.current_limit):
                    self.semaphore.release()

    async def on_throttle(self):
        async with self._lock:
            self.current_limit = max(self.min_limit, self.current_limit * settings.AIMD_BACKOFF_FACTOR)
            logger.warn("Adaptive throttle triggered: Reducing concurrency", new_limit=int(self.current_limit))


class NetworkEngine:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.host_limiters: dict[str, AdaptiveSemaphore] = {}
        self.default_limiter = AdaptiveSemaphore(settings.DEFAULT_HOST_LIMIT)

    async def initialize(self):
        connector = aiohttp.TCPConnector(
            limit=settings.TOTAL_CONCURRENT_LIMIT,
            ttl_dns_cache=settings.DNS_CACHE_TTL,
            enable_cleanup_closed=True
        )
        self.session = aiohttp.ClientSession(connector=connector)
        
        for host, limit in settings.HOST_LIMITS.items():
            self.host_limiters[host] = AdaptiveSemaphore(
                limit, 
                min_limit=settings.MIN_HOST_LIMIT, 
                max_limit=settings.MAX_HOST_LIMIT
            )

    async def close(self):
        if self.session:
            await self.session.close()

    def _get_limiter(self, url: str) -> AdaptiveSemaphore:
        host = urlparse(url).netloc
        return self.host_limiters.get(host, self.default_limiter)

    def _get_random_proxy(self) -> Optional[str]:
        return random.choice(settings.PROXY_POOL) if settings.PROXY_POOL else None

    async def fetch(
        self,
        url: str,
        method: str = "GET",
        data: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None
    ) -> Optional[str]:
        if not self.session:
            raise RuntimeError("Network Engine uninitialized.")

        limiter = self._get_limiter(url)
        req_headers = {**settings.USER_AGENTS[hash(url) % len(settings.USER_AGENTS)], **(headers or {})}
        
        await limiter.acquire()
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(settings.MAX_RETRIES),
                wait=wait_exponential_jitter(initial=1, max=16),
                retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
                reraise=False
            ):
                with attempt:
                    proxy = self._get_random_proxy()
                    async with self.session.request(
                        method,
                        url,
                        data=data,
                        headers={"User-Agent": settings.USER_AGENTS[random.randint(0, len(settings.USER_AGENTS) - 1)], **(headers or {})},
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=settings.REQUEST_TIMEOUT)
                    ) as resp:
                        if resp.status == 404:
                            return None
                        
                        if resp.status in (429, 503):
                            await limiter.on_throttle()
                            raise aiohttp.ClientResponseError(
                                request_info=resp.request_info,
                                history=resp.history,
                                status=resp.status,
                                message="Rate Limited / Service Unavailable"
                            )
                        
                        resp.raise_for_status()
                        await limiter.on_success()
                        return await resp.text()
        except Exception as exc:
            logger.error("Request failed after max retries", url=url, error=str(exc))
            return None
        finally:
            limiter.release()

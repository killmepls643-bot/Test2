"""
Pipeline Configuration & Dynamic Settings Management.
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Pipeline Metadata
    SOURCE_NAME: str = "VN & Nyaa Universal Source"
    OUTPUT_FILE: Path = Path("source.json")
    DB_FILE: Path = Path("pipeline_cache.db")
    
    # Global Concurrency & Network Parameters
    TOTAL_CONCURRENT_LIMIT: int = 100
    DEFAULT_HOST_LIMIT: int = 10
    REQUEST_TIMEOUT: float = 20.0
    MAX_RETRIES: int = 5
    DNS_CACHE_TTL: int = 300
    
    # Adaptive Dynamic Scaling (AIMD) Settings
    AIMD_BACKOFF_FACTOR: float = 0.5
    AIMD_INCREASE_STEP: float = 1.0
    MIN_HOST_LIMIT: int = 2
    MAX_HOST_LIMIT: int = 25
    
    # Target Host Rate Limits
    HOST_LIMITS: dict[str, int] = {
        "nyaa.si": 6,
        "sukebei.nyaa.si": 6,
        "www.ryuugames.com": 10,
    }

    # Proxy Configuration (Empty list defaults to direct connections)
    PROXY_POOL: list[str] = [
        # Example: "http://user:pass@proxy1.example.com:8080",
        # Example: "socks5://proxy2.example.com:1080"
    ]

    USER_AGENTS: list[str] = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    ]

    class Config:
        env_prefix = "HYDRA_"
        case_sensitive = True


settings = Settings()

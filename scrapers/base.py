"""
Abstract Base Class for Pipeline Scrapers.
"""

from abc import ABC, abstractmethod
from typing import AsyncGenerator

from models import DownloadEntry
from network import NetworkEngine


class BaseScraper(ABC):
    def __init__(self, net: NetworkEngine):
        self.net = net

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the scraper target."""
        pass

    @abstractmethod
    async def run(self) -> AsyncGenerator[list[DownloadEntry], None]:
        """Runs scraper yielding chunks of DownloadEntry items for processing."""
        yield []

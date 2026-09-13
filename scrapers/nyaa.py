"""
Optimized Nyaa.si & Sukebei Scraper utilizing Selectolax for high-performance DOM parsing.
"""

import math
import re
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

import structlog
from selectolax.parser import HTMLParser

from models import DownloadEntry
from network import NetworkEngine
from scrapers.base import BaseScraper

logger = structlog.get_logger(__name__)

SIZE_REGEX = re.compile(r"(\d+(?:\.\d+)?)\s*([KMGT]i?B)", re.IGNORECASE)
NYAA_PAGINATION_REGEX = re.compile(r"[?&]p=(\d+)")


def parse_bytes(size_str: str) -> tuple[str, int]:
    """Standardizes binary file size strings and converts them to raw bytes."""
    if not size_str or size_str == "Unknown":
        return "Unknown", 0
    match = SIZE_REGEX.search(size_str)
    if not match:
        return size_str.strip(), 0
    
    val, unit = float(match.group(1)), match.group(2).upper()
    unit_clean = unit.replace("IB", "B")
    
    multipliers = {
        "B": 1,
        "KB": 1000, "KIB": 1024,
        "MB": 1000**2, "MIB": 1024**2,
        "GB": 1000**3, "GIB": 1024**3,
        "TB": 1000**4, "TIB": 1024**4,
    }
    bytes_val = int(val * multipliers.get(unit, 1))
    return f"{val:.1f} {unit_clean}", bytes_val


class NyaaScraper(BaseScraper):
    def __init__(self, net: NetworkEngine, base_url: str, category: str, label: str):
        super().__init__(net)
        self.base_url = base_url
        self.category = category
        self.label = label

    @property
    def name(self) -> str:
        return self.label

    async def get_max_pages(self) -> int:
        url = f"{self.base_url}/?c={self.category}&p=1"
        html = await self.net.fetch(url)
        if not html:
            return 1
        
        parser = HTMLParser(html)
        max_page = 1
        for a in parser.css("ul.pagination li a"):
            href = a.attributes.get("href", "")
            match = NYAA_PAGINATION_REGEX.search(href)
            if match:
                max_page = max(max_page, int(match.group(1)))
        return max_page

    async def parse_page(self, page: int) -> list[DownloadEntry]:
        url = f"{self.base_url}/?c={self.category}&p={page}"
        html = await self.net.fetch(url)
        if not html:
            return []

        parser = HTMLParser(html)
        results = []
        for row in parser.css("table.torrent-list tbody tr"):
            try:
                # Title parsing
                title_nodes = row.css("td[colspan='2'] a:not(.comments)")
                if not title_nodes:
                    continue
                title = title_nodes[-1].text(strip=True)

                # Magnet link parsing
                magnet_node = row.css_first("a[href^='magnet:']")
                if not magnet_node:
                    continue
                magnet_uri = magnet_node.attributes.get("href", "")

                # Table fields: Size, Date
                tds = row.css("td")
                file_size_raw = tds[3].text(strip=True) if len(tds) > 3 else "Unknown"
                raw_date = tds[4].text(strip=True) if len(tds) > 4 else ""

                formatted_size, size_bytes = parse_bytes(file_size_raw)
                
                upload_date = ""
                if raw_date:
                    try:
                        dt = datetime.strptime(raw_date, "%Y-%m-%d %H:%M")
                        upload_date = dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    except Exception:
                        upload_date = raw_date

                entry = DownloadEntry(
                    title=title,
                    uris=[magnet_uri],
                    fileSize=formatted_size,
                    fileSizeBytes=size_bytes,
                    uploadDate=upload_date,
                    category=self.label
                )
                results.append(entry)
            except Exception as err:
                logger.debug("Parsing row skipped due to error", error=str(err))
                continue
        return results

    async def run(self) -> AsyncGenerator[list[DownloadEntry], None]:
        logger.info(f"[{self.label}] Scanning pagination limits...")
        max_pages = await self.get_max_pages()
        logger.info(f"[{self.label}] Found total {max_pages} pages to scrape.")

        chunk_size = 20
        for i in range(1, max_pages + 1, chunk_size):
            pages = range(i, min(i + chunk_size, max_pages + 1))
            tasks = [self.parse_page(p) for p in pages]
            results = await asyncio.gather(*tasks)
            
            chunk_entries = []
            for res in results:
                chunk_entries.extend(res)
            
            yield chunk_entries

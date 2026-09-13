"""
RyuuGames Scraper featuring Bounded Recursive Search & Processing Token Solvers.
"""

import base64
import json
from typing import AsyncGenerator, Optional, Set

import structlog
from selectolax.parser import HTMLParser

from models import DownloadEntry
from network import NetworkEngine
from scrapers.base import BaseScraper
from scrapers.nyaa import parse_bytes

logger = structlog.get_logger(__name__)


class RyuuGamesScraper(BaseScraper):
    def __init__(self, net: NetworkEngine):
        super().__init__(net)
        self.base_url = "https://www.ryuugames.com"

    @property
    def name(self) -> str:
        return "RyuuGames Visual Novels"

    async def resolve_download_link(self, page_url: str, link_key: str, post_id: str, shortcode_id: str) -> Optional[str]:
        proc_url = f"{self.base_url}/processing/"
        payload = {
            "ryuu_sl_action": "process",
            "post_id": post_id,
            "shortcode_id": shortcode_id,
            "link_key": link_key,
        }
        headers = {"Referer": page_url}
        html = await self.net.fetch(proc_url, method="POST", data=payload, headers=headers)
        if not html:
            return None

        try:
            parser = HTMLParser(html)
            form = parser.css_first("form#continueForm")
            if not form:
                return None
            
            host_input = parser.css_first("input[name='host']")
            if not host_input or not host_input.attributes.get("value"):
                return None

            raw_val = host_input.attributes["value"]
            decoded_json = json.loads(base64.b64decode(raw_val).decode("utf-8"))
            return decoded_json.get("url")
        except Exception as exc:
            logger.debug("Failed to resolve Ryuu tokenized link", error=str(exc))
            return None

    async def parse_game_page(self, url: str) -> Optional[DownloadEntry]:
        html = await self.net.fetch(url)
        if not html:
            return None

        parser = HTMLParser(html)
        h1 = parser.css_first("h1")
        if not h1:
            return None

        title = h1.text(strip=True)
        
        # Scoped content size extraction
        file_size_str = "Unknown"
        size_bytes = 0
        container = parser.css_first(".entry-content, .post-content, article")
        if container:
            file_size_str, size_bytes = parse_bytes(container.text())

        # Resolve buttons
        buttons = parser.css("button[data-link-key]")
        seen_groups: Set[str] = set()
        tasks = []

        for btn in buttons:
            lk = btn.attributes.get("data-link-key")
            pid = btn.attributes.get("data-post-id")
            sid = btn.attributes.get("data-shortcode-id")
            if not (lk and pid and sid):
                continue

            group_prefix = lk.split("_")[0]
            if group_prefix in seen_groups:
                continue
            seen_groups.add(group_prefix)

            tasks.append(self.resolve_download_link(url, lk, pid, sid))

        resolved_uris = await asyncio.gather(*tasks)
        uris = [u for u in resolved_uris if u]
        if not uris:
            return None

        return DownloadEntry(
            title=title,
            uris=uris,
            fileSize=file_size_str,
            fileSizeBytes=size_bytes,
            category="RyuuGames"
        )

    async def discover_urls(self, path: str) -> set[str]:
        urls: set[str] = set()
        html = await self.net.fetch(f"{self.base_url}{path}")
        if not html:
            return urls

        parser = HTMLParser(html)
        for a in parser.css("h3 a[href*='ryuugames.com']"):
            href = a.attributes.get("href")
            if href:
                urls.add(href)

        # Detect total pages safely
        max_page = 1
        for a in parser.css("a.page-numbers, ul.pagination a"):
            href = a.attributes.get("href", "")
            if "/page/" in href:
                try:
                    p_num = int(href.split("/page/")[1].split("/")[0])
                    max_page = max(max_page, p_num)
                except ValueError:
                    pass

        # Bounded iteration ceiling replacing raw infinite failure counting
        async def fetch_cat_page(p: int) -> set[str]:
            res_html = await self.net.fetch(f"{self.base_url}{path.rstrip('/')}/page/{p}/")
            if not res_html:
                return set()
            p_parser = HTMLParser(res_html)
            return {a.attributes.get("href") for a in p_parser.css("h3 a[href*='ryuugames.com']") if a.attributes.get("href")}

        for batch_start in range(2, max_page + 1, 10):
            batch_pages = range(batch_start, min(batch_start + 10, max_page + 1))
            res = await asyncio.gather(*[fetch_cat_page(p) for p in batch_pages])
            for link_set in res:
                urls.update(link_set)

        return urls

    async def run(self) -> AsyncGenerator[list[DownloadEntry], None]:
        logger.info("[RyuuGames] Starting link discovery phase...")
        categories = ["/", "/category/visualnovel/english-translated/"]
        
        discovered_tasks = [self.discover_urls(c) for c in categories]
        results = await asyncio.gather(*discovered_tasks)
        all_urls = list(set().union(*results))
        
        logger.info(f"[RyuuGames] Discovered {len(all_urls)} game entry pages.")

        chunk_size = 15
        for i in range(0, len(all_urls), chunk_size):
            chunk = all_urls[i:i + chunk_size]
            entries = await asyncio.gather(*[self.parse_game_page(u) for u in chunk])
            valid_entries = [e for e in entries if e is not None]
            yield valid_entries

"""
Pydantic v2 Models enforcing strict Hydra Launcher Schemas & Magnet parsing.
"""

import base64
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field, field_validator


INFOHASH_V1_HEX_REGEX = re.compile(r"^[a-fA-F0-9]{40}$")
INFOHASH_V1_B32_REGEX = re.compile(r"^[a-zA-Z2-7]{32}$")
INFOHASH_V2_HEX_REGEX = re.compile(r"^[a-fA-F0-9]{64}$")


class DownloadEntry(BaseModel):
    title: str = Field(..., min_length=1)
    uris: list[str] = Field(default_factory=list)
    fileSize: str = Field(default="Unknown")
    fileSizeBytes: int = Field(default=0)
    uploadDate: Optional[str] = None
    infoHash: Optional[str] = None
    category: str = Field(default="General")

    @field_validator("uris", mode="before")
    def validate_and_sanitize_uris(cls, value: list[str]) -> list[str]:
        sanitized = []
        for uri in value:
            uri_str = str(uri).strip()
            if uri_str.startswith(("http://", "https://", "magnet:")):
                sanitized.append(uri_str)
        if not sanitized:
            raise ValueError("DownloadEntry must contain at least one valid HTTP or Magnet URI.")
        return list(dict.fromkeys(sanitized))  # Deduplicate while preserving order

    @field_validator("infoHash", mode="before", always=True)
    def extract_and_normalize_hash(cls, v: Optional[str], values: dict) -> Optional[str]:
        if v:
            return v.lower()
        
        # Auto-extract from magnet URIs if missing
        uris = values.data.get("uris", []) if hasattr(values, "data") else values.get("uris", [])
        for uri in uris:
            if uri.startswith("magnet:"):
                parsed = urlparse(uri)
                query_params = parse_qs(parsed.query)
                xt_list = query_params.get("xt", [])
                for xt in xt_list:
                    if xt.startswith("urn:btih:"):
                        raw_hash = xt.replace("urn:btih:", "").strip()
                        if INFOHASH_V1_HEX_REGEX.match(raw_hash):
                            return raw_hash.lower()
                        elif INFOHASH_V1_B32_REGEX.match(raw_hash):
                            try:
                                padded = raw_hash.upper() + "=" * (-len(raw_hash) % 8)
                                return base64.b32decode(padded).hex().lower()
                            except Exception:
                                pass
                    elif xt.startswith("urn:btmh:"):
                        raw_hash = xt.replace("urn:btmh:", "").strip()
                        if INFOHASH_V2_HEX_REGEX.match(raw_hash):
                            return raw_hash.lower()
        return None

    def get_dedupe_key(self) -> str:
        """Computes deterministic primary key priority: InfoHash -> Canonical HTTP URI -> Normalized Title."""
        if self.infoHash:
            return f"hash:{self.infoHash}"
        
        for uri in self.uris:
            if uri.startswith(("http://", "https://")):
                parsed = urlparse(uri)
                clean_path = f"{parsed.netloc}{parsed.path}".rstrip("/").lower()
                return f"uri:{clean_path}"

        clean_title = re.sub(r"[^\w\s]", "", self.title).lower()
        clean_title = re.sub(r"\s+", " ", clean_title).strip()
        return f"title:{clean_title}"


class HydraSourceMetadata(BaseModel):
    name: str
    downloads: list[DownloadEntry] = Field(default_factory=list)

"""
Transactional SQLite Storage Engine utilizing Write-Ahead Logging (WAL) mode.
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional

import aiosqlite
import orjson
import structlog

from config import settings
from models import DownloadEntry

logger = structlog.get_logger(__name__)


class StorageEngine:
    def __init__(self, db_path: Path = settings.DB_FILE, json_output: Path = settings.OUTPUT_FILE):
        self.db_path = db_path
        self.json_output = json_output
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        """Initializes SQLite connection with high-performance WAL & sync settings."""
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        await self._db.execute("PRAGMA temp_store=MEMORY;")
        
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS records (
                dedupe_key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                file_size TEXT,
                file_size_bytes INTEGER,
                upload_date TEXT,
                info_hash TEXT,
                category TEXT,
                uris_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_info_hash ON records(info_hash);")
        await self._db.commit()
        logger.info("Database initialized successfully", db_path=str(self.db_path))

    async def close(self):
        if self._db:
            await self._db.close()
            logger.info("Database connection closed cleanly.")

    async def upsert_batch(self, entries: list[DownloadEntry]) -> tuple[int, int]:
        """Atomically upserts entries in a single transaction."""
        if not entries or not self._db:
            return 0, 0

        added = 0
        updated = 0

        async with self._db.execute("BEGIN TRANSACTION;"):
            for entry in entries:
                key = entry.get_dedupe_key()
                
                # Fetch existing record for merge comparison
                cursor = await self._db.execute(
                    "SELECT uris_json, file_size, file_size_bytes, upload_date FROM records WHERE dedupe_key = ?",
                    (key,)
                )
                row = await cursor.fetchone()

                if row is None:
                    # Insert new record
                    uris_json = json.dumps(entry.uris)
                    await self._db.execute(
                        """
                        INSERT INTO records 
                        (dedupe_key, title, file_size, file_size_bytes, upload_date, info_hash, category, uris_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (key, entry.title, entry.fileSize, entry.fileSizeBytes, entry.uploadDate, entry.infoHash, entry.category, uris_json)
                    )
                    added += 1
                else:
                    # Merge logic with existing record
                    existing_uris = set(json.loads(row[0]))
                    new_uris = [u for u in entry.uris if u not in existing_uris]
                    merged_uris = list(existing_uris.union(entry.uris))
                    
                    # Target metadata fallbacks
                    file_size = entry.fileSize if (row[1] in (None, "Unknown") and entry.fileSize != "Unknown") else row[1]
                    file_size_bytes = entry.fileSizeBytes if (row[2] == 0 and entry.fileSizeBytes > 0) else row[2]
                    upload_date = entry.uploadDate if not row[3] else row[3]

                    if new_uris or file_size != row[1] or upload_date != row[3]:
                        await self._db.execute(
                            """
                            UPDATE records 
                            SET uris_json = ?, file_size = ?, file_size_bytes = ?, upload_date = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE dedupe_key = ?
                            """,
                            (json.dumps(merged_uris), file_size, file_size_bytes, upload_date, key)
                        )
                        updated += 1

        await self._db.commit()
        return added, updated

    async def export_to_hydra_json(self):
        """Exports the SQLite database into standard Hydra Launcher output JSON format."""
        if not self._db:
            raise RuntimeError("Database not initialized.")

        logger.info("Building final Hydra Launcher output JSON file...", destination=str(self.json_output))
        
        downloads = []
        async with self._db.execute("SELECT title, uris_json, file_size, upload_date FROM records") as cursor:
            async for row in cursor:
                downloads.append({
                    "title": row[0],
                    "uris": json.loads(row[1]),
                    "fileSize": row[2] or "Unknown",
                    "uploadDate": row[3] or ""
                })

        output_data = {
            "name": settings.SOURCE_NAME,
            "downloads": downloads
        }

        # Fast atomic file write using orjson
        tmp_output = self.json_output.with_suffix(".tmp")
        with open(tmp_output, "wb") as f:
            f.write(orjson.dumps(output_data, option=orjson.OPT_INDENT_2))
        
        tmp_output.replace(self.json_output)
        logger.info("Hydra JSON file export complete.", total_records=len(downloads))

    async def get_total_count(self) -> int:
        if not self._db:
            return 0
        async with self._db.execute("SELECT COUNT(*) FROM records") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

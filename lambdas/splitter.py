import os
import uuid
from typing import Any

from config import settings
from io_operations.google_sheets import (
    GoogleSheetsHandler,
    ProductionSheetStrategy,
    RegularSheetStrategy,
)
from logger import logger

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "5"))


def handler(event: dict, context: Any) -> dict:
    """Split domains from the sheet into chunks for parallel processing."""
    workflow_mode = os.environ.get("WORKFLOW_MODE", "regular")
    handler_ = GoogleSheetsHandler(settings.spreadsheet_info)
    strategy = (
        ProductionSheetStrategy(handler_)
        if workflow_mode == "production"
        else RegularSheetStrategy(handler_)
    )
    seen_records, unseen_records = strategy.get_records()

    job_id = str(uuid.uuid4())
    chunks = []
    for i in range(0, len(unseen_records), CHUNK_SIZE):
        chunk_records = unseen_records[i : i + CHUNK_SIZE]
        chunks.append(
            {
                "chunk_id": i // CHUNK_SIZE,
                "domains": [{"domain": r.company_url, "row_no": r.row_no} for r in chunk_records],
                "workflow_mode": workflow_mode,
                "job_id": job_id,
            }
        )

    logger.info(f"Split {len(unseen_records)} records into {len(chunks)} chunks (job_id={job_id})")
    return {
        "workflow_mode": workflow_mode,
        "job_id": job_id,
        "chunks": chunks,
        "skipped_records": seen_records,
    }

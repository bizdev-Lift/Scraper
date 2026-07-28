import json
from typing import Any

from _types import DomainInput
from config import settings
from io_operations.google_sheets import (
    GoogleSheetsHandler,
    ProductionSheetStrategy,
    RegularSheetStrategy,
)
from io_operations.s3_client import S3Client
from logger import logger

s3 = S3Client()


def handler(event: dict, context: Any) -> dict:
    """Read staged worker results from S3, batch save to sheets, delete processed rows."""
    workflow_mode = event.get("workflow_mode", "regular")
    job_id = event.get("job_id", "")

    if not job_id:
        logger.error("No job_id in event")
        return {"saved": 0, "deleted": 0}

    prefix = f"staging/{job_id}/"
    keys = s3.list_objects(prefix)
    if not keys:
        logger.info(f"No staged results found at {prefix}")
        return {"saved": 0, "deleted": 0}

    all_processed = []
    all_failed = []
    for key in keys:
        try:
            content = s3.read_content(key)
            if content is None:
                continue
            data = json.loads(content)
            all_processed.extend(data.get("processed", []))
            all_failed.extend(data.get("failed", []))
        except Exception as e:
            logger.error(f"Failed to parse s3://{s3.bucket_name}/{key}: {e}")

    if not all_processed and not all_failed:
        logger.info("No results to save")
        return {"saved": 0, "deleted": 0}

    handler_ = GoogleSheetsHandler(settings.spreadsheet_info)
    strategy = (
        ProductionSheetStrategy(handler_)
        if workflow_mode == "production"
        else RegularSheetStrategy(handler_)
    )

    strategy.save_results(all_processed, all_failed)

    saved_count = 0

    if workflow_mode == "production" and all_processed:
        records = [
            DomainInput(row_no=item["row_no"], company_url=item["domain"]) for item in all_processed
        ]
        strategy.on_complete(records, job_id=job_id)
        saved_count = len(records)
        logger.info(f"Cleanup complete: {saved_count} saved, rows deleted")

    # 2. Delete S3 staging files
    if job_id:
        keys = s3.list_objects(f"staging/{job_id}/")
        if keys:
            s3.delete_objects(keys)
            logger.info(f"Cleaned up {len(keys)} staging files for job {job_id}")
        else:
            logger.info(f"No staging files found for job {job_id}")

    logger.info(f"Job {job_id}: {saved_count} saved, {len(all_failed)} failed")
    return {"saved": saved_count, "deleted": saved_count}

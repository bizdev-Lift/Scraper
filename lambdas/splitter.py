import uuid
from typing import Any

from config import settings
from io_operations.google_sheets import (
    GoogleSheetsHandler,
    ProductionSheetStrategy,
    RegularSheetStrategy,
)
from io_operations.s3_client import S3Client
from logger import logger


def _generate_job_id(workflow_mode: str) -> str:
    s3 = S3Client()
    for _ in range(10):
        job_id = str(uuid.uuid4())
        if not s3.list_objects(f"staging/{workflow_mode}/{job_id}/"):
            return job_id
    raise RuntimeError("Failed to generate unique job_id after 10 attempts")


def handler(event: dict, context: Any) -> dict:
    """Split domains from the sheet into chunks for parallel processing."""
    workflow_mode = event["workflow_mode"]
    handler_ = GoogleSheetsHandler(settings.spreadsheet_info)
    strategy = (
        ProductionSheetStrategy(handler_)
        if workflow_mode == "production"
        else RegularSheetStrategy(handler_)
    )
    job_id = _generate_job_id(workflow_mode)
    logger.info(f"Generated job_id={job_id} for workflow_mode={workflow_mode}")
    return strategy.build_response(job_id)

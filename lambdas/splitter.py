import uuid
from typing import Any

from io_operations.record_stores import create_store
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
    """Split records into chunks for parallel processing.

    hubspot mode reads its input from HubSpot; regular/production read
    from Google Sheets.
    """
    workflow_mode = event["workflow_mode"]
    job_id = _generate_job_id(workflow_mode)
    logger.info(f"Generated job_id={job_id} for workflow_mode={workflow_mode}")
    return create_store(workflow_mode).build_response(job_id)

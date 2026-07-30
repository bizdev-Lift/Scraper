import uuid
from typing import Any

from config import settings
from io_operations.google_sheets import (
    GoogleSheetsHandler,
    ProductionSheetStrategy,
    RegularSheetStrategy,
)


def handler(event: dict, context: Any) -> dict:
    """Split domains from the sheet into chunks for parallel processing."""
    workflow_mode = event["workflow_mode"]
    handler_ = GoogleSheetsHandler(settings.spreadsheet_info)
    strategy = (
        ProductionSheetStrategy(handler_)
        if workflow_mode == "production"
        else RegularSheetStrategy(handler_)
    )
    job_id = str(uuid.uuid4())
    return strategy.build_response(job_id)

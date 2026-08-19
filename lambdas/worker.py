import json
from dataclasses import asdict
from typing import Any

from _types import DomainInput
from executor import MainExecutor
from io_operations.record_stores import HubSpotDataMapper, create_store
from io_operations.s3_client import S3Client
from logger import logger

s3 = S3Client()


def handler(event: dict, context: Any) -> dict:
    """Process a chunk of domains and stage results to S3 for batch save.

    Workers never open Google Sheets (`MainExecutor(open_store=False)`) -
    doing so would burn Google Sheets API quota (429s) across concurrent
    workers. Results are staged to S3 and persisted by the cleanup Lambda.
    In hubspot mode, results are also pushed straight back to HubSpot via
    the store's on_success/on_error hooks.
    """
    domains = event.get("domains", [])
    workflow_mode = event.get("workflow_mode", "regular")
    chunk_id = event.get("chunk_id", 0)
    job_id = event.get("job_id", "")

    if not domains:
        return {"chunk_id": chunk_id, "domain_count": 0, "status": "empty"}

    executor = MainExecutor(workflow_mode=workflow_mode, open_store=False)
    store = create_store(workflow_mode) if workflow_mode == "hubspot" else None
    processed = []
    failed = []
    for item in domains:
        record = DomainInput(
            company_url=item["domain"],
            company_name=item.get("company_name", item["domain"]),
            row_no=item.get("row_no", 0),
            hubspot_id=item.get("hubspot_id"),
        )
        logger.info(
            f"Worker processing domain={record.company_url} "
            f"(row={record.row_no}, hubspot_id={record.hubspot_id})"
        )

        try:
            mode, result = executor.process_domain(record.company_url)
            if mode == "success":
                processed.append(
                    {
                        "domain": record.company_url,
                        "row_no": record.row_no,
                        "hubspot_id": record.hubspot_id,
                        "data": asdict(result),
                        "hubspot_payload": HubSpotDataMapper.map_to_hubspot_payload(record, result),
                    }
                )
                if store:
                    store.on_success(record, result)
                logger.info(f"Successfully processed domain={record.company_url}")
            else:
                failed.append(
                    {
                        "domain": record.company_url,
                        "row_no": record.row_no,
                        "hubspot_id": record.hubspot_id,
                        "data": asdict(result),
                        "error": result.lead_status,
                    }
                )
                if store:
                    store.on_error(record, result)
                logger.error(f"Failed to process domain={record.company_url}")
        except Exception as e:
            failed.append(
                {
                    "domain": record.company_url,
                    "row_no": record.row_no,
                    "hubspot_id": record.hubspot_id,
                    "error": str(e),
                }
            )
            logger.exception(f"Error processing domain={record.company_url}")

    s3_key = f"staging/{workflow_mode}/{job_id}/{chunk_id}.json"
    s3.write_raw_content(s3_key, json.dumps({"processed": processed, "failed": failed}))

    logger.info(
        f"Chunk {chunk_id} complete: {len(processed)} processed, "
        f"{len(failed)} failed — staged to s3://{s3.bucket_name}/{s3_key}"
    )
    return {"chunk_id": chunk_id, "domain_count": len(processed), "status": "ok"}

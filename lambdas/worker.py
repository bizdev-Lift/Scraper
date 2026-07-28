import json
from dataclasses import asdict
from typing import Any

from _types import DomainInput
from executor import MainExecutor
from io_operations.google_sheets import HubSpotDataMapper
from io_operations.s3_client import S3Client
from logger import logger

s3 = S3Client()


def handler(event: dict, context: Any) -> dict:
    """Process a chunk of domains and stage results to S3 for batch save."""
    domains = event.get("domains", [])
    workflow_mode = event.get("workflow_mode", "regular")
    job_id = event.get("job_id", "")
    chunk_id = event.get("chunk_id", 0)

    if not domains:
        return {"chunk_id": chunk_id, "domain_count": 0, "status": "empty"}

    executor = MainExecutor(workflow_mode=workflow_mode)
    processed = []
    failed = []

    domain_urls = [item["domain"] for item in domains]
    apollo_results = {}
    seamless_results = {}
    if executor.strategy.pre_enrich:
        apollo_results = executor.apollo_api.enrich_leads(domain_urls)
        seamless_results = executor.seamless_api.enrich_leads(domain_urls)

    for item in domains:
        record = DomainInput(
            company_url=item["domain"], company_name=item["domain"], row_no=item["row_no"]
        )
        logger.info(f"Worker processing domain={record.company_url} (row={record.row_no})")

        try:
            apollo_result = seamless_result = None
            if executor.strategy.pre_enrich:
                apollo_result = apollo_results.get(record.company_url)
                seamless_result = seamless_results.get(record.company_url)

            mode, result = executor.process_domain(
                record.company_url,
                apollo_result=apollo_result,
                seamless_result=seamless_result,
            )
            if mode == "success":
                processed.append(
                    {
                        "domain": record.company_url,
                        "row_no": record.row_no,
                        "data": asdict(result),
                        "hubspot_payload": HubSpotDataMapper.map_to_hubspot_payload(record, result),
                    }
                )
                logger.info(f"Successfully processed domain={record.company_url}")
            else:
                failed.append(
                    {
                        "domain": record.company_url,
                        "row_no": record.row_no,
                        "data": asdict(result),
                        "error": "Processing failed because of an error.",
                    }
                )
                logger.error(f"Failed to process domain={record.company_url}")
        except Exception as e:
            failed.append(
                {
                    "domain": record.company_url,
                    "row_no": record.row_no,
                    "error": str(e),
                }
            )
            logger.exception(f"Error processing domain={record.company_url}")

    s3_key = f"staging/{job_id}/{chunk_id}.json"
    s3.write_raw_content(s3_key, json.dumps({"processed": processed, "failed": failed}))

    logger.info(
        f"Chunk {chunk_id} complete: {len(processed)} processed, "
        f"{len(failed)} failed — staged to s3://{s3.bucket_name}/{s3_key}"
    )
    return {"chunk_id": chunk_id, "domain_count": len(processed), "status": "ok"}

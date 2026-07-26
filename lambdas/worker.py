from typing import Any

from _types import DomainInput
from executor import MainExecutor
from logger import logger


def handler(event: dict, context: Any) -> dict:
    """Process a chunk of domains."""
    domains = event.get("domains", [])
    workflow_mode = event.get("workflow_mode", "regular")

    if not domains:
        return {"processed": [], "failed": []}

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

            result = executor.process_domain(
                record.company_url,
                apollo_result=apollo_result,
                seamless_result=seamless_result,
            )

            if result:
                executor.strategy.on_success(record, result)
                processed.append({"domain": record.company_url, "row_no": record.row_no})
                logger.info(f"Successfully processed domain={record.company_url}")
            else:
                failed.append(
                    {
                        "domain": record.company_url,
                        "row_no": record.row_no,
                        "error": "Processing returned no result",
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
            logger.exception(f"Error processing domain={record.omain_url}")

    logger.info(f"Chunk complete: {len(processed)} processed, {len(failed)} failed")
    return {"processed": processed, "failed": failed}

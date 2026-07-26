from typing import Any

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

    apollo_results = executor.apollo_api.enrich_leads(domains)
    seamless_results = executor.seamless_api.enrich_leads(domains)
    for item in domains:
        domain_url = item["domain"]
        row_no = item["row_no"]
        logger.info(f"Worker processing domain={domain_url} (row={row_no})")

        try:
            apollo_result = seamless_result = None
            if executor.strategy.pre_enrich:
                apollo_result = apollo_results.get(domain_url)
                seamless_result = seamless_results.get(domain_url)

            result = executor.process_domain(
                domain_url,
                apollo_result=apollo_result,
                seamless_result=seamless_result,
            )

            if result:
                processed.append({"domain": domain_url, "row_no": row_no})
                logger.info(f"Successfully processed domain={domain_url}")
            else:
                failed.append(
                    {
                        "domain": domain_url,
                        "row_no": row_no,
                        "error": "Processing returned no result",
                    }
                )
                logger.error(f"Failed to process domain={domain_url}")

        except Exception as e:
            failed.append(
                {
                    "domain": domain_url,
                    "row_no": row_no,
                    "error": str(e),
                }
            )
            logger.exception(f"Error processing domain={domain_url}")

    logger.info(f"Chunk complete: {len(processed)} processed, {len(failed)} failed")
    return {"processed": processed, "failed": failed}

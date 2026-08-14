"""Local testing entrypoint for the Lambda handlers.

Toggle TEST_HANDLER to select which handler to run, then:
    python main.py

The sample payloads below mirror what the Step Functions pipeline sends to
each Lambda at runtime. A .env / exported env vars must be present for config
to load, otherwise the script fails on import.
"""

import json
from typing import Any

from lambdas.cleanup import handler as cleanup_handler
from lambdas.splitter import handler as splitter_handler
from lambdas.worker import handler as worker_handler

TEST_HANDLER = "worker"  # one of: splitter, worker, cleanup

JOB_ID = "debug-session"
WORKFLOW_MODE = "regular"

# Splitter only receives the workflow mode.
SPLITTER_INPUT: dict = {"workflow_mode": WORKFLOW_MODE}

# Each Map iteration hands the Worker a single chunk.
WORKER_INPUT: dict = {
    "chunk_id": 0,
    "domains": [
        {"domain": "dtidirect.com", "row_no": 99},
        {"domain": "accesstoindependence.com", "row_no": 301},
        {"domain": "west20.com", "row_no": 307},
        {"domain": "toolstoday.com", "row_no": 396},
        {"domain": "ringor.com", "row_no": 541},
    ],
    "workflow_mode": WORKFLOW_MODE,
    "job_id": JOB_ID,
}

# Cleanup receives the full state-machine payload (chunks + Map results).
CLEANUP_INPUT: dict = {
    "workflow_mode": WORKFLOW_MODE,
    "job_id": JOB_ID,
    "chunks": [
        {
            "chunk_id": 0,
            "domains": [
                {"domain": "dtidirect.com", "row_no": 99},
                {"domain": "accesstoindependence.com", "row_no": 301},
                {"domain": "west20.com", "row_no": 307},
                {"domain": "toolstoday.com", "row_no": 396},
                {"domain": "ringor.com", "row_no": 541},
            ],
            "workflow_mode": WORKFLOW_MODE,
            "job_id": JOB_ID,
        },
        {
            "chunk_id": 1,
            "domains": [
                {"domain": "surplusautomationequipment.com", "row_no": 578},
                {"domain": "maisonette.com", "row_no": 649},
                {"domain": "speedzone-web.com", "row_no": 842},
                {"domain": "rusticsforless.com", "row_no": 845},
                {"domain": "mightyautoparts.com", "row_no": 948},
            ],
            "workflow_mode": WORKFLOW_MODE,
            "job_id": JOB_ID,
        },
        {
            "chunk_id": 2,
            "domains": [
                {"domain": "plasticingenuity.com", "row_no": 949},
                {"domain": "renohd.com", "row_no": 950},
                {"domain": "alom.com", "row_no": 951},
                {"domain": "msf-usa.org", "row_no": 952},
                {"domain": "cranecarrier.com", "row_no": 953},
            ],
            "workflow_mode": WORKFLOW_MODE,
            "job_id": JOB_ID,
        },
        {
            "chunk_id": 3,
            "domains": [
                {"domain": "gradolabs.com", "row_no": 954},
                {"domain": "mastrack.com", "row_no": 955},
                {"domain": "collegestationford.com", "row_no": 956},
                {"domain": "acmedistribution.com", "row_no": 957},
                {"domain": "outreach770.com", "row_no": 958},
            ],
            "workflow_mode": WORKFLOW_MODE,
            "job_id": JOB_ID,
        },
    ],
    "results": [
        {"chunk_id": 0, "domain_count": 3, "status": "ok"},
        {"chunk_id": 1, "domain_count": 5, "status": "ok"},
        {"chunk_id": 2, "domain_count": 3, "status": "ok"},
        {"chunk_id": 3, "domain_count": 4, "status": "ok"},
    ],
}

HANDLERS: dict[str, Any] = {
    "splitter": splitter_handler,
    "worker": worker_handler,
    "cleanup": cleanup_handler,
}

INPUTS: dict[str, dict] = {
    "splitter": SPLITTER_INPUT,
    "worker": WORKER_INPUT,
    "cleanup": CLEANUP_INPUT,
}


def main() -> None:
    if TEST_HANDLER not in HANDLERS:
        raise ValueError(f"Unknown TEST_HANDLER: {TEST_HANDLER}. Must be one of {list(HANDLERS)}")
    result = HANDLERS[TEST_HANDLER](INPUTS[TEST_HANDLER], None)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

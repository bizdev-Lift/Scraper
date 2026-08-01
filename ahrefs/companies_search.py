import json
import time

import requests

from _types import AhrefsResult
from config import settings
from logger import logger


class AhrefsAPI:
    BASE_API_ENDPOINT = "https://api.ahrefs.com"
    AHREFS_BASE_API_VERSION = "v3"
    BATCH_ANALYSIS_ENDPOINT = (
        f"{BASE_API_ENDPOINT}/{AHREFS_BASE_API_VERSION}/batch-analysis/batch-analysis"
    )

    HEADERS = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.ahrefs_api_key}",
    }

    # Confirm this against your actual plan (Standard/Advanced/Enterprise differ:
    # 200/500/1000 in some docs, 100 in others). Verify before a large run.
    MAX_TARGETS_PER_REQUEST = 100

    # Ahrefs API is capped at 60 requests/minute by default.
    REQUEST_PACING_SECONDS = 1.1

    SELECT_FIELDS = [
        "org_traffic_top_by_country",
        "org_traffic",
        "paid_traffic",
        "org_keywords",
        "paid_keywords",
        "backlinks",
        "refdomains",
    ]
    # SELECT_FIELDS = ["ahrefs_rank", "backlinks", "backlinks_dofollow", "backlinks_internal", "backlinks_nofollow", "backlinks_redirect", "domain_rating", "index", "ip", "linked_domains", "linked_domains_dofollow", "mode", "org_cost", "org_keywords", "org_keywords_11_20", "org_keywords_1_3", "org_keywords_21_50", "org_keywords_4_10", "org_keywords_51_plus", "org_traffic", "org_traffic_top_by_country", "outgoing_links", "outgoing_links_dofollow", "paid_ads", "paid_cost", "paid_keywords", "paid_traffic", "protocol", "refdomains", "refdomains_dofollow", "refdomains_nofollow", "refips", "refips_subnets", "url", "url_rating"]

    def __init__(self):
        pass

    def enrich_lead(self, domain: str) -> AhrefsResult:
        target_output = {}
        try:
            r = requests.post(
                self.BATCH_ANALYSIS_ENDPOINT,
                headers=self.HEADERS,
                json={
                    "select": self.SELECT_FIELDS,
                    "targets": [{"url": domain, "mode": "subdomains", "protocol": "both"}],
                    "volume_mode": "monthly",
                    "output": "json",
                },
            )
            if r.status_code == 401:
                logger.error("[Ahrefs] 401 — check your API key")
            elif r.status_code == 422:
                logger.error(f"[Ahrefs] 422 — {r.text[:120]}")
            elif r.status_code == 429:
                logger.error("[Ahrefs] 429 — rate limited")

            r.raise_for_status()
            results = r.json().get("targets") or []
            target_output = results[0] if results else {}
        except Exception:
            logger.exception("Failure to fetch data from Ahrefs")

        return self._format_ahrefs(target_output)

    def enrich_leads(self, domains: list[str]) -> dict[str, AhrefsResult]:
        if not domains:
            return {}

        result: dict[str, AhrefsResult] = {}

        for chunk in self._chunk(domains, self.MAX_TARGETS_PER_REQUEST):
            chunk_result = self._enrich_chunk(chunk)
            result.update(chunk_result)
            time.sleep(self.REQUEST_PACING_SECONDS)

        return result

    def _enrich_chunk(self, domains: list[str]) -> dict[str, AhrefsResult]:
        targets_output = []
        try:
            r = requests.post(
                self.BATCH_ANALYSIS_ENDPOINT,
                headers=self.HEADERS,
                json={
                    "select": self.SELECT_FIELDS,
                    "targets": [
                        {"url": domain, "mode": "subdomains", "protocol": "both"}
                        for domain in domains
                    ],
                    "volume_mode": "monthly",
                    "output": "json",
                },
            )
            if r.status_code == 401:
                logger.error("[Ahrefs] 401 — check your API key")
            elif r.status_code == 422:
                logger.error(f"[Ahrefs] 422 — {r.text[:120]}")
            elif r.status_code == 429:
                logger.error("[Ahrefs] 429 — rate limited, back off and retry")
            else:
                r.raise_for_status()
                targets_output = r.json().get("targets") or []
        except Exception:
            logger.exception("Failure to fetch data from Ahrefs")

        result = {}
        for domain, target in zip(domains, targets_output):
            if target is None:
                logger.info(f"[Ahrefs] No data returned for '{domain}'")
            result[domain] = self._format_ahrefs(target or {})

        return result

    @staticmethod
    def _chunk(items: list, size: int):
        for i in range(0, len(items), size):
            yield items[i : i + size]

    @staticmethod
    def _format_ahrefs(t: dict) -> AhrefsResult:
        if not t:
            return AhrefsResult()
        t["org_traffic_top_by_country"] = json.dumps(t["org_traffic_top_by_country"])
        return AhrefsResult(**t)

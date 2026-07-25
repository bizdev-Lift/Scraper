from typing import Optional

import requests

BASE_URL = "https://api.hubapi.com"

HUBSPOT_BATCH_LIMIT = 100


class HubSpotCompaniesClient:
    def __init__(self, api_key: Optional[str] = None):
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict, mode="get") -> dict:
        resp = requests.post(BASE_URL + path, headers=self.headers, json=body)
        resp.raise_for_status()
        return resp.json()

    def get_existing_domains(self, domains: list[str]) -> set[str]:
        """
        Given a list of domains, returns only those NOT present in HubSpot.
        Chunks into batches of 5 (HubSpot filterGroups limit).
        """
        found = set()

        if not domains:
            return found

        # Process in chunks of 5 (HubSpot max filterGroups per request)
        # for i in range(0, len(domains), 20):
        # chunk = domains[i:i + 5]

        body = {
            "filterGroups": [
                {"filters": [{"propertyName": "name", "operator": "IN", "values": domains}]}
            ],
            "properties": ["name"],
            "limit": 100,
        }

        data = self._post("/crm/v3/objects/companies/search", body)
        for r in data.get("results", []):
            domain = r.get("properties", {}).get("name")
            if domain:
                found.add(domain.lower())

        return set(found)

    def add_company(self, record: dict) -> dict:
        """
        Create or update a single company in HubSpot, keyed by domain.

        record: e.g.
            ``{"domain": "example.com", "properties": {"name": "Example Inc", "industry": "SAAS"}}``

        Thin wrapper around add_companies() for the single-record case.
        Returns:
            {"success": True, "result": {...}} on success
            {"success": False, "error": {...}} on failure
        """
        combined = self.add_companies([record])

        if combined["errors"]:
            return {"success": False, "error": combined["errors"][0]}

        results = combined["results"]
        return {"success": True, "result": results[0] if results else None}

    def add_companies(self, records: list[dict]) -> dict:
        """
        Create or update companies in HubSpot, keyed by domain.

        records: list of dicts, e.g.
            [{"domain": "example.com", "properties": {"name": "Example Inc",
             "industry": "SAAS"}}, ...]

        Uses HubSpot's batch upsert endpoint (idProperty="domain"), so a company
        is created if that domain doesn't exist yet, or updated if it does -
        no need to look up object IDs first.

        Batches into groups of 100 (HubSpot's per-request batch limit).
        Returns a dict with combined "results" and any "errors" encountered.
        """
        combined = {"results": [], "errors": []}

        if not records:
            return combined

        for i in range(0, len(records), HUBSPOT_BATCH_LIMIT):
            chunk = records[i : i + HUBSPOT_BATCH_LIMIT]

            inputs = [
                {
                    # "idProperty": "domain",
                    # "id": record["domain"],
                    "properties": record["properties"],
                }
                for record in chunk
            ]

            body = {"inputs": inputs}

            try:
                # data = self._post("/crm/v3/objects/companies/batch/upsert", body)
                data = self._post("/crm/v3/objects/companies/batch/create", body, mode="post")

                combined["results"].extend(data.get("results", []))
            except requests.HTTPError as e:
                # Capture the failed chunk instead of aborting the whole run -
                # lets you retry just the failed domains later.
                combined["errors"].append(
                    {
                        "chunk_domains": [r["domain"] for r in chunk],
                        "status_code": e.response.status_code if e.response is not None else None,
                        "detail": e.response.text if e.response is not None else str(e),
                    }
                )

        return combined

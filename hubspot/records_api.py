import os
import requests
from typing import Optional

BASE_URL = "https://api.hubapi.com"


class HubSpotCompaniesClient:
    def __init__(self, api_key: Optional[str] = None):
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict) -> dict:
        resp = requests.post(BASE_URL + path, headers=self.headers, json=body)
        resp.raise_for_status()
        return resp.json()

    def get_existing_domains(self, domains: list[str]) -> list[str]:
        """
        Given a list of domains, returns only those NOT present in HubSpot.
        Chunks into batches of 5 (HubSpot filterGroups limit).
        """
        found = set()
 
        # Process in chunks of 5 (HubSpot max filterGroups per request)
        # for i in range(0, len(domains), 20):
        # chunk = domains[i:i + 5]
 
        body = {
            "filterGroups": [
                {
                    "filters": [{
                        "propertyName": "domain",
                        "operator": "IN",
                        "values": domains
                    }]
                }
            ],
            "properties": ["domain"],
            "limit": 100,
        }

        data = self._post("/crm/v3/objects/companies/search", body)
        print(data)
        for r in data.get("results", []):
            domain = r.get("properties", {}).get("domain")
            if domain:
                found.add(domain.lower())

        return found

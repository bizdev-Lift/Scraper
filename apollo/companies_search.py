import re
import requests

from config import settings
from logger import logger
from _types import ApolloResult

class ApolloAPI:
    BASE_API_ENDPOINT = "https://api.apollo.io"
    APOLLO_BASE_API_VERSION = "v1"
    ORGANIZATION_ENRICH_ENDPOINT = f"{BASE_API_ENDPOINT}/{APOLLO_BASE_API_VERSION}/organizations/enrich"
    ORGANIZATION_BULK_ENRICH_ENDPOINT = f"{BASE_API_ENDPOINT}/{APOLLO_BASE_API_VERSION}/organizations/bulk_enrich"
    HEADERS = {
        "Content-Type": "application/json",
        "X-Api-Key": settings.apollo_api_key
    }
    def __init__(self):
        pass
        
    def enrich_lead(self, domain: str) -> ApolloResult:
        organization_output = {}
        try:
            r = requests.post(
                self.ORGANIZATION_ENRICH_ENDPOINT,
                headers=self.HEADERS,
                json={"domain": domain}
            )     
            if r.status_code == 401:
                logger.error("[Apollo] 401 — check your API key")
            elif r.status_code == 422:
                logger.error(f"[Apollo] 422 — {r.text[:120]}")
                
            r.raise_for_status()
            organization_output = r.json().get("organization") or {}         
        except Exception:
            logger.exception("Failure to fetch data from Apollo")
        
        return self._format_apollo(organization_output)
        
    def enrich_leads(self, domains: list[str]) -> dict[str: ApolloResult]:
        if not domains:
            return {}
        
        organizations_output = {}
        try:
            r = requests.post(
                self.ORGANIZATION_BULK_ENRICH_ENDPOINT,
                headers=self.HEADERS,
                json={"domains": domains}
            )     
            if r.status_code == 401:
                logger.error("[Apollo] 401 — check your API key")
            elif r.status_code == 422:
                logger.error(f"[Apollo] 422 — {r.text[:120]}")
            else:
                r.raise_for_status()
                organizations_output = r.json().get("organizations") or {}         
        except Exception:
            logger.exception("Failure to fetch data from Apollo")
        
        # return {
        #     domain: self._format_apollo(org) 
        #     for org, domain in zip(organizations_output, domains)
        # }
        # Match orgs back to requested domains by value, not position —
        # Apollo may omit unmatched domains or reorder results.
        by_domain = {}
        for org in organizations_output:
            if not org:
                continue 
            
            returned_domain = org.get("primary_domain") or org.get("domain")
            if returned_domain:
                by_domain[returned_domain] = org

        result = {}
        for requested_domain in domains:
            org = by_domain.get(requested_domain)
            if org is None:
                logger.info(f"[Apollo] No organization returned for '{requested_domain}'")
            result[requested_domain] = self._format_apollo(org or {})

        return result
        
    @staticmethod
    def _format_apollo(o: dict) -> ApolloResult:
        if not o:
            return ApolloResult()
    
        website_url = o.get("website_url")
        if website_url:
            website_url = re.sub(r"http(s)?://(www\.)?", "", website_url)

        ph_no = o.get("phone")
        if ph_no:
            ph_no = re.sub("^\+1\s?", "", ph_no)
            
        return ApolloResult(
            company_name=o.get("name"),
            num_of_employees=o.get("estimated_num_employees"),
            industry=o.get("industry"),
            website=website_url,
            company_linkedin_url=o.get("linkedin_url"),
            company_state=o.get("state"),
            company_country=o.get("country"),
            company_postal_code=o.get("postal_code"),
            company_phone=ph_no,
            annual_revenue=o.get("organization_revenue"),
            num_of_retail_locations=o.get("retail_location_count"),
            short_description=o.get("short_description"),
        )

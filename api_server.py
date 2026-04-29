from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Literal
import uvicorn
from contextlib import asynccontextmanager

from executor import MainExecutor
from _types import DomainInput, DomainResponse
from logger import logger


# Pydantic models for API
class ProcessRequest(BaseModel):
    domain_url: str


# Global executor variable
executor = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan."""
    # Startup
    global executor
    try:
        executor = MainExecutor()
        logger.info("MainExecutor initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize MainExecutor: {e}")
        raise

    yield

    # Shutdown
    logger.info("API server shutting down")


# Initialize FastAPI app
app = FastAPI(
    title="LLM Data Extraction API",
    description="API for extracting company information from websites using LLM",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"message": "LLM Data Extraction API is running"}


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "message": "API is running normally"}


@app.post("/process", response_model=DomainResponse)
async def process_company(request: ProcessRequest):
    """
    Process a company URL and extract information.

    Args:
        request: ProcessRequest containing company_name and company_url

    Returns:
        ProcessResponse: Extracted company information
    """
    try:
        # Convert request to DomainInput
        domain_input = DomainInput(
            row_no="0",
            company_name=request.domain_url,
            company_url=request.domain_url,
        )

        logger.info(f"API request received for url: {request.domain_url}")

        # Process the record
        default_summary_json = executor._get_default_summary()
        result = executor.process_record(domain_input, default_summary_json)

        logger.info(f"Successfully processed company: {request.domain_url}")
        return result

    except Exception as e:
        logger.error(f"Error processing company {request.domain_url}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


if __name__ == "__main__":
    uvicorn.run(
        "api_server:app", host="0.0.0.0", port=8000, reload=True, log_level="info"
    )

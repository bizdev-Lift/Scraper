def clean_llm_response_json_data(data: str) -> str | None:
    return data.replace("```json", "").replace("`", "")

from dataclasses import dataclass
from typing import Any, TypeAlias
from io import StringIO


GeminiJSONStructure: TypeAlias = dict[
    str, list[dict[str, dict[str, Any]]] | dict[str, Any]
]


class GeminiChatCompletion:
    def __init__(self, chat_completion: "GeminiResponse") -> None:
        self.chat_completion = chat_completion

    def completion(self) -> str | None:
        return self.chat_completion.get_text()

    def prompt_tokens(self) -> int | None:
        if self.chat_completion.usage_metadata:
            return self.chat_completion.usage_metadata.prompt_token_count
        return None

    def completion_tokens(self) -> int | None:
        if self.chat_completion.usage_metadata:
            total_tokens = self.chat_completion.usage_metadata.candidates_token_count
            if self.chat_completion.usage_metadata.thoughts_token_count:
                total_tokens += self.chat_completion.usage_metadata.thoughts_token_count
            return total_tokens
        return None

    def cost(self, input_tokens, output_tokens) -> int | None:
        million = 1000000
        input_token_cost = 0.5
        output_token_cost = 3.0

        input_cost = (input_tokens / million) * input_token_cost
        output_cost = (output_tokens / million) * output_token_cost
        return input_cost + output_cost


@dataclass(frozen=True)
class GeminiResponse:
    candidates: list["GeminiResponseCandidate"]
    usage_metadata: "GeminiResponseUsageMetadata | None"

    @staticmethod
    def from_dict(input: GeminiJSONStructure) -> "GeminiResponse | None":
        candidates = GeminiResponse._parse_candidates(input)
        usage_metadata = GeminiResponse._parse_usage_metadata(input)

        if not candidates:
            return None

        return GeminiResponse(candidates=candidates, usage_metadata=usage_metadata)

    def get_text(self) -> str | None:
        response_text = StringIO()

        if not self.candidates:
            return None

        for candidate in self.candidates:
            content = candidate.content

            if not content:
                continue

            parts = content.parts
            if not parts:
                continue

            for part in parts:
                text = part.text

                if not text:
                    continue

                response_text.write(text)

        value = response_text.getvalue()
        if not value:
            return None
        return value

    @staticmethod
    def _parse_candidates(
        gemini_response: GeminiJSONStructure,
    ) -> list["GeminiResponseCandidate"] | None:
        candidates = gemini_response.get("candidates")

        if not isinstance(candidates, list):
            return None

        parsed_candidates = [GeminiResponseCandidate.from_dict(c) for c in candidates]
        return [pc for pc in parsed_candidates if pc]

    @staticmethod
    def _parse_usage_metadata(
        gemini_response: GeminiJSONStructure,
    ) -> "GeminiResponseUsageMetadata | None":
        usage_metadata = gemini_response.get("usageMetadata")

        if not isinstance(usage_metadata, dict):
            return None

        return GeminiResponseUsageMetadata.from_dict(usage_metadata)


@dataclass(frozen=True)
class GeminiResponseUsageMetadata:
    prompt_token_count: int
    candidates_token_count: int
    total_token_count: int
    thoughts_token_count: int | None = None

    @staticmethod
    def from_dict(input: dict[str, int]) -> "GeminiResponseUsageMetadata | None":
        prompt_token_count = input.get("promptTokenCount")
        candidates_token_count = input.get("candidatesTokenCount")
        total_token_count = input.get("totalTokenCount")
        thoughts_token_count = input.get("thoughtsTokenCount")

        if (
            not prompt_token_count
            or not candidates_token_count
            or not total_token_count
        ):
            return None

        return GeminiResponseUsageMetadata(
            prompt_token_count=prompt_token_count,
            candidates_token_count=candidates_token_count,
            total_token_count=total_token_count,
            thoughts_token_count=thoughts_token_count
        )


@dataclass(frozen=True)
class GeminiResponseCandidate:
    content: "GeminiResponseCandidateContent"

    @staticmethod
    def from_dict(input: dict[str, dict[str, Any]]) -> "GeminiResponseCandidate | None":
        content = input.get("content")

        if not content:
            return None

        parsed_content = GeminiResponseCandidateContent.from_dict(content)
        if parsed_content:
            return GeminiResponseCandidate(content=parsed_content)

        return None


@dataclass(frozen=True)
class GeminiResponseCandidateContent:
    parts: list["GeminiResponseCandidateContentPart"]

    @staticmethod
    def from_dict(
        input: dict[str, list[dict[str, str]]],
    ) -> "GeminiResponseCandidateContent | None":
        parts = input.get("parts")
        if not parts:
            return None
        parsed_parts = [GeminiResponseCandidateContentPart.from_dict(p) for p in parts]
        return GeminiResponseCandidateContent(parts=[p for p in parsed_parts if p])


@dataclass(frozen=True)
class GeminiResponseCandidateContentPart:
    text: str

    @staticmethod
    def from_dict(input: dict[str, str]) -> "GeminiResponseCandidateContentPart | None":
        text = input.get("text")
        if not text:
            return None

        return GeminiResponseCandidateContentPart(text=text)

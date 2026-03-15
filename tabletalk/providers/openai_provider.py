from typing import Dict, Generator, List, Optional

from openai import OpenAI

from tabletalk.interfaces import LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        max_tokens: int = 1000,
        temperature: float = 0.0,
        base_url: Optional[str] = None,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)

    def generate_response(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        content = response.choices[0].message.content
        return content.strip() if content is not None else ""

    def generate_response_stream(self, prompt: str) -> Generator[str, None, None]:
        yield from self.generate_chat_stream([{"role": "user", "content": prompt}])

    def generate_chat_stream(
        self, messages: List[Dict[str, str]]
    ) -> Generator[str, None, None]:
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            stream=True,
        )
        for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                yield content

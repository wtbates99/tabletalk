"""
openai_provider.py — OpenAI and Ollama LLM provider.

item 25: Token usage is captured from the streaming response via
         stream_options={"include_usage": True} (OpenAI SDK >= 1.26)
         and stored in self.last_usage for QuerySession to persist.
"""
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
        super().__init__()
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
        # Capture token usage (item 25)
        if response.usage:
            self.last_usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            }
        content = response.choices[0].message.content
        return content.strip() if content is not None else ""

    def generate_response_stream(self, prompt: str) -> Generator[str, None, None]:
        yield from self.generate_chat_stream([{"role": "user", "content": prompt}])

    def generate_chat_stream(
        self, messages: List[Dict[str, str]]
    ) -> Generator[str, None, None]:
        # Request usage data in the final streaming chunk (item 25)
        # stream_options is supported by OpenAI SDK >= 1.26; ignored by Ollama.
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stream=True,
                stream_options={"include_usage": True},
            )
        except TypeError:
            # Older SDK or Ollama that doesn't accept stream_options
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stream=True,
            )

        for chunk in stream:
            # The final chunk from OpenAI contains usage when stream_options is set
            if chunk.usage:
                self.last_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                }
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

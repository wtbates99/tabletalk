"""
anthropic_provider.py — Anthropic Claude LLM provider.

item 25: Token usage is captured from the stream's final message object via
         stream.get_final_message().usage and stored in self.last_usage.
"""
from typing import Dict, Generator, List

from anthropic import Anthropic

from tabletalk.interfaces import LLMProvider


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ):
        super().__init__()
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client = Anthropic(api_key=api_key)

    def generate_response(self, prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        # Capture token usage (item 25)
        if response.usage:
            self.last_usage = {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
            }
        if not response.content:
            return ""
        content = response.content[0]
        text = content.text if hasattr(content, "text") else str(content)
        return text.strip() if text else ""

    def generate_response_stream(self, prompt: str) -> Generator[str, None, None]:
        yield from self.generate_chat_stream([{"role": "user", "content": prompt}])

    def generate_chat_stream(
        self, messages: List[Dict[str, str]]
    ) -> Generator[str, None, None]:
        # Anthropic separates system messages from conversation turns
        system = ""
        turns = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                turns.append(m)

        kwargs: dict = {
            "model": self.model,
            "messages": turns,
            "max_tokens": self.max_tokens,
        }
        if system:
            kwargs["system"] = system

        with self.client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield text

            # After the stream closes, capture token usage (item 25)
            try:
                final = stream.get_final_message()
                if final and final.usage:
                    self.last_usage = {
                        "prompt_tokens": final.usage.input_tokens,
                        "completion_tokens": final.usage.output_tokens,
                    }
            except Exception:
                pass

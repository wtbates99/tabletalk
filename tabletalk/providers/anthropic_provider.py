from anthropic import Anthropic

from tabletalk.interfaces import LLMProvider


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20240620"):
        self.api_key = api_key
        self.model = model
        self.client = Anthropic(api_key=api_key)

    def generate_response(self, prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0,
        )

        if hasattr(response.content[0], "text"):
            content = response.content[0].text
        else:
            content = str(response.content[0])

        return content.strip() if content is not None else ""

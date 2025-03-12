from openai import OpenAI

from tabletalk.interfaces import LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-3.5-turbo"):
        self.api_key = api_key
        self.model = model
        self.client = OpenAI(api_key=api_key)

    def generate_response(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0,
        )
        content = response.choices[0].message.content
        return content.strip() if content is not None else ""

from typing import List, Optional

import openai
from interfaces import LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model

    def generate_sql(
        self,
        schema: str,
        question: str,
        focus_tables: Optional[List[str]] = None,
        max_tokens: int = 150,
        temperature: float = 0,
    ) -> str:
        focus_str = f"Focus tables: {', '.join(focus_tables)}" if focus_tables else ""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a BigQuery SQL expert. Your goal is to generate a valid BigQuery SQL query that answers the question.",
                },
                {
                    "role": "user",
                    "content": f"""
                    The schema below is in compact JSON:

                    Schema: {schema}

                    {focus_str}

                    Question: "{question}"

                    Generate a valid BigQuery SQL query.
                """,
                },
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = response.choices[0].message.content
        return content.strip() if content is not None else ""

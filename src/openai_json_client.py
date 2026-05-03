from __future__ import annotations

import json
from typing import Any, Dict

from openai import OpenAI


class OpenAIJsonError(RuntimeError):
    pass


class OpenAIJsonClient:
    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key)

    def generate_json(self, *, model: str, system_prompt: str, user_prompt: str, max_output_tokens: int, temperature: float) -> Dict[str, Any]:
        try:
            resp = self.client.responses.create(
                model=model,
                input=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )
        except Exception as e:
            msg = str(e)
            if "model" in msg.lower() and ("not found" in msg.lower() or "invalid" in msg.lower()):
                raise OpenAIJsonError(f"ERROR: OpenAI model is not available or invalid: {model}. Please set model to a valid model id.")
            raise
        text = getattr(resp, "output_text", "") or ""
        return self.parse_or_repair(text, model=model, max_output_tokens=max_output_tokens, temperature=temperature)

    def parse_or_repair(self, text: str, *, model: str, max_output_tokens: int, temperature: float) -> Dict[str, Any]:
        try:
            return json.loads(text)
        except Exception:
            repair = self.client.responses.create(
                model=model,
                input=[{"role": "user", "content": f"Return strict JSON only.\n{text}"}],
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )
            return json.loads(getattr(repair, "output_text", ""))

from __future__ import annotations

import json
import os
from typing import Any, Dict

from openai import OpenAI


class OpenAIJsonError(RuntimeError):
    pass


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None or not value.strip() else float(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None or not value.strip() else int(value)


class OpenAIJsonClient:
    def __init__(self, api_key: str):
        timeout_seconds = _env_float("OPENAI_REQUEST_TIMEOUT_SECONDS", 180.0)
        max_retries = _env_int("OPENAI_MAX_RETRIES", 1)
        self.client = OpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    def _responses_kwargs(
        self,
        *,
        model: str,
        input_payload: list[dict[str, str]],
        max_output_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": model,
            "input": input_payload,
            "max_output_tokens": max_output_tokens,
        }

        # GPT-5 family can reject custom temperature in some API/project configurations.
        # Keep default temperature for GPT-5 models unless explicitly handled elsewhere.
        if not model.startswith("gpt-5"):
            kwargs["temperature"] = temperature

        return kwargs

    def generate_json(self, *, model: str, system_prompt: str, user_prompt: str, max_output_tokens: int, temperature: float) -> Dict[str, Any]:
        try:
            resp = self.client.responses.create(
                **self._responses_kwargs(
                    model=model,
                    input_payload=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                )
            )
        except Exception as e:
            msg = str(e)
            raise OpenAIJsonError(
                f"OpenAI request failed: model={model}; error_type={type(e).__name__}; error={msg}"
            ) from e

        text = getattr(resp, "output_text", "") or ""
        return self.parse_or_repair(text, model=model, max_output_tokens=max_output_tokens, temperature=temperature)

    def parse_or_repair(self, text: str, *, model: str, max_output_tokens: int, temperature: float) -> Dict[str, Any]:
        try:
            return json.loads(text)
        except Exception:
            try:
                repair = self.client.responses.create(
                    **self._responses_kwargs(
                        model=model,
                        input_payload=[{"role": "user", "content": f"Return strict JSON only.\n{text}"}],
                        max_output_tokens=max_output_tokens,
                        temperature=temperature,
                    )
                )
            except Exception as e:
                msg = str(e)
                raise OpenAIJsonError(
                    f"OpenAI JSON repair failed: model={model}; error_type={type(e).__name__}; error={msg}"
                ) from e

            return json.loads(getattr(repair, "output_text", ""))

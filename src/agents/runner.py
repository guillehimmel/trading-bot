import json
import re
import threading
from pathlib import Path

import anthropic

from config.settings import ANTHROPIC_API_KEY

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# $ por millón de tokens (precios de lista de Anthropic). Si agregás otro
# modelo, sumalo acá o get_usage_summary() no puede estimar su costo.
_PRICING_PER_MTOK = {
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
}

_usage_lock = threading.Lock()
_usage_log: list[dict] = []


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def reset_usage() -> None:
    with _usage_lock:
        _usage_log.clear()


def get_usage_summary() -> dict:
    """Gasto real acumulado desde el último reset_usage(), con costo estimado
    a precio de lista por modelo."""
    with _usage_lock:
        entries = list(_usage_log)

    by_model: dict[str, dict] = {}
    total_cost = 0.0
    for e in entries:
        m = by_model.setdefault(e["model"], {"input_tokens": 0, "output_tokens": 0, "calls": 0})
        m["input_tokens"] += e["input_tokens"]
        m["output_tokens"] += e["output_tokens"]
        m["calls"] += 1

        pricing = _PRICING_PER_MTOK.get(e["model"])
        if pricing:
            total_cost += e["input_tokens"] / 1_000_000 * pricing["input"]
            total_cost += e["output_tokens"] / 1_000_000 * pricing["output"]

    return {
        "calls": len(entries),
        "by_model": by_model,
        "total_input_tokens": sum(m["input_tokens"] for m in by_model.values()),
        "total_output_tokens": sum(m["output_tokens"] for m in by_model.values()),
        "estimated_cost_usd": round(total_cost, 4),
    }


def run_agent(prompt_name: str, input_data: dict, model: str, max_tokens: int = 8000) -> dict:
    """Runs one department agent and returns its parsed JSON output.

    prompt_name: file stem under src/agents/prompts/ (e.g. "ceo", "mercado").
    model: siempre explícito — evita usar por accidente el modelo caro (CEO)
    en un llamado de departamento que debería ir por el barato (worker).
    """
    system_prompt = (PROMPTS_DIR / f"{prompt_name}.md").read_text()

    response = _client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": json.dumps(input_data, ensure_ascii=False)}],
    )

    with _usage_lock:
        _usage_log.append(
            {
                "prompt_name": prompt_name,
                "model": model,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
        )

    text = "".join(block.text for block in response.content if block.type == "text")
    cleaned = _strip_fences(text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"El agente '{prompt_name}' no devolvió JSON válido "
            f"(stop_reason={response.stop_reason}, modelo={model}): {text!r}"
        ) from exc

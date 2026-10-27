"""One Muse Spark turn with function tools, for the authoring agent. MetaApi has no tool-calling request, so this
sends one through MetaApi's client and keeps MetaApi's rules: reserve on the run's cost meter first, log every attempt
to the run's calls.jsonl with its cost, and retry rate limits and server errors a few times."""
import time

from openai import APIConnectionError, InternalServerError, RateLimitError

from pipeline import config
from pipeline.cost import spark_cost

TURN_ESTIMATE_USD = 0.02
ATTEMPTS = 3
RETRYABLE = (RateLimitError, APIConnectionError, InternalServerError)


def make_responder(api, tools: list[dict], sleep=time.sleep):
    def respond(items: list[dict]) -> dict:
        """{"output": the turn's items as dicts, to send back next turn, "text": the turn's text}."""
        for attempt in range(ATTEMPTS):
            api.meter.reserve(TURN_ESTIMATE_USD)
            spent, t0 = 0.0, time.time()
            try:
                resp = api.client.responses.create(model=config.SPARK_MODEL, input=items, tools=tools)
                usage = resp.usage.model_dump() if resp.usage is not None else {}
                spent = spark_cost(usage)
                api.log("spark_agent", seconds=time.time() - t0, cost_usd=spent, usage=usage)
                return {"output": [o.model_dump() for o in resp.output], "text": resp.output_text or ""}
            except RETRYABLE as exc:
                api.log("spark_agent_failed", seconds=time.time() - t0, error=f"{type(exc).__name__}: {exc}"[:300])
                if attempt == ATTEMPTS - 1:
                    raise
                sleep(2 ** attempt)
            finally:
                api.meter.settle(TURN_ESTIMATE_USD, spent)
        raise RuntimeError("unreachable")

    return respond

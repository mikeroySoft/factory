"""Offline response-boundary check: python experiments/jev-triage/check.py."""
from copy import deepcopy
from run import validate

criteria = {"accept": "Supported", "defer": "Uncertain"}
response = {
    "model": "test",
    "answers": {"route": {"type": "choice", "choice": "accept",
                           "probabilities": {"accept": 0.8, "defer": 0.2}, "confidence": 0.6}},
    "usage": {"input_tokens": 100, "output_tokens": 10},
}
assert validate(response, criteria)["choice"] == "accept"
for field, value in [("confidence", float("nan")), ("choice", "invented"),
                     ("probabilities", {"accept": 1.0}),
                     ("probabilities", {"accept": 0.8, "defer": 0.8})]:
    invalid = deepcopy(response)
    invalid["answers"]["route"][field] = value
    try:
        validate(invalid, criteria)
    except ValueError:
        pass
    else:
        raise AssertionError(f"Accepted invalid {field}")
invalid = deepcopy(response)
invalid["usage"]["input_tokens"] = 1_000_001
try:
    validate(invalid, criteria)
except ValueError:
    pass
else:
    raise AssertionError("Accepted usage above reservation")
print("Response boundary checks passed")

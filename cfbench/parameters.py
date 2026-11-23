"""External intervention data, with a deliberately narrow registered domain."""
import json
import re
from decimal import Decimal
from pathlib import Path

DEFAULT_PARAMS = Path(__file__).resolve().parents[1] / "configs/obm036.json"


def load_parameters(path=DEFAULT_PARAMS):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema") != "cfbench-obm036-parameters-v1":
        raise ValueError("Unsupported parameter schema")
    entries = raw.get("interventions")
    if not isinstance(entries, list) or not entries:
        raise ValueError("At least one intervention is required")
    result, used = [], set()
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"id", "prior_millions"}:
            raise ValueError("Each intervention must specify id and prior_millions only")
        name = item["id"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or name in used:
            raise ValueError("Intervention ids must be unique safe directory names")
        text = str(item["prior_millions"])
        # One decimal matches the original document's comparator display precision.
        if not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9])?", text):
            raise ValueError("prior_millions must be positive, <=9 integer digits, <=1 decimal place")
        prior = Decimal(text)
        if prior <= 0 or prior == Decimal(2300):
            raise ValueError("Comparator must be positive and differ from the original 2300")
        used.add(name)
        result.append({"id": name, "prior_millions": str(prior)})
    return result


def numeric_prior(item):
    value = Decimal(item["prior_millions"])
    return int(value) if value == value.to_integral_value() else float(value)

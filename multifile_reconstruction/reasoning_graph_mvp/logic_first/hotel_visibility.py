"""Select and verify public evidence for a finite, executable business graph."""
from fractions import Fraction
from itertools import combinations, product


SCOPE = (
    "Minimum parallel operator depth over registered productions and guarded "
    "set short-circuits. Sources have depth zero; one operator over vectors "
    "counts as one layer. The search cannot exhaust natural-language shortcuts "
    "or unstated business knowledge."
)


def to_json(value):
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, dict):
        return {key: to_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_json(item) for item in value]
    return value


def _number(value):
    if isinstance(value, bool):
        raise TypeError("Boolean is not a quantity")
    return Fraction(str(value))


def _equal(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, (set, frozenset)) or isinstance(right, (set, frozenset)):
        if not isinstance(left, (set, frozenset, tuple, list)) or not isinstance(right, (set, frozenset, tuple, list)):
            return False
        return set(left) == set(right)
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    try:
        return _number(left) == _number(right)
    except (TypeError, ValueError, ZeroDivisionError):
        return left == right


def _compare(left, right, comparison):
    left, right = _number(left), _number(right)
    if comparison == "GE":
        return left >= right
    if comparison == "LE":
        return left <= right
    if comparison == "GT":
        return left > right
    if comparison == "LT":
        return left < right
    if comparison == "EQ":
        return left == right
    raise ValueError(f"Unknown comparison {comparison}")


def _compute(rule, values, universe):
    op = rule["op"]
    if op == "COUNT":
        return len(values[0])
    if op == "AVG":
        members, vector = values
        return sum((_number(vector[key]) for key in members), Fraction(0)) / len(members)
    if op in {"AND", "OR"}:
        left, right = (frozenset(value) for value in values)
        return left & right if op == "AND" else left | right
    if op in {"GE", "LE", "COMPARE"}:
        left, right = values
        comparison = rule.get("comparison", op)
        return frozenset(
            key for key in universe
            if _compare(left[key], right[key] if isinstance(right, dict) else right, comparison)
        )
    if op in {"ADD", "SUB", "MUL", "DIV"}:
        left, right = values

        def arithmetic(a, b):
            a, b = _number(a), _number(b)
            if op == "ADD":
                return a + b
            if op == "SUB":
                return a - b
            if op == "MUL":
                return a * b
            return a / b

        if isinstance(left, dict) or isinstance(right, dict):
            return {
                key: arithmetic(left[key] if isinstance(left, dict) else left, right[key] if isinstance(right, dict) else right)
                for key in sorted(universe)
            }
        return arithmetic(left, right)
    raise ValueError(f"Unregistered operator {op}")


def _candidates(rule, known, universe):
    inputs = rule["inputs"]
    op = rule["op"]
    if all(name in known for name in inputs):
        try:
            yield inputs, _compute(rule, [known[name]["value"] for name in inputs], universe), None
        except ZeroDivisionError:
            pass
    for name in inputs:
        if name not in known:
            continue
        value = known[name]["value"]
        if op == "AND" and not value:
            yield [name], frozenset(), "empty-set short-circuit"
        if op == "OR" and frozenset(value) == universe:
            yield [name], universe, "universal-set short-circuit"


def solve_public(graph, observations, rules=(), targets=None):
    """Close verified rules over actual public observations; never read private truth."""
    targets = list(graph["targets"] if targets is None else targets)
    known = {
        node: {
            "value": value, "depth": 0, "production": None, "observations": frozenset({node}),
            "rule_evidence": frozenset(), "production_ids": frozenset(), "inputs": [],
        }
        for node, value in observations.items()
    }
    available_rules = set(rules)
    if "entities" in graph:
        universe = frozenset(graph["entities"])
    else:
        universe = frozenset(key for value in observations.values() if isinstance(value, dict) for key in value)
    productions = sorted(graph["productions"], key=lambda production: production["id"])
    conflicts = {}
    applied = {}
    while True:
        changed = False
        for rule in productions:
            required = frozenset(rule.get("requires", ()))
            if not required <= available_rules:
                continue
            for inputs, result, shortcut in _candidates(rule, known, universe):
                parents = [known[name] for name in inputs]
                depth = 1 + max((parent["depth"] for parent in parents), default=0)
                production = rule["id"]
                evidence = frozenset().union(*(parent["observations"] for parent in parents))
                used_rules = required.union(*(parent["rule_evidence"] for parent in parents))
                used_productions = frozenset({production}).union(*(parent["production_ids"] for parent in parents))
                proof = {
                    "value": result, "depth": depth, "production": production, "inputs": list(inputs),
                    "observations": evidence, "rule_evidence": used_rules, "production_ids": used_productions,
                }
                key = f"{production}:short:{inputs[0]}" if shortcut else production
                if shortcut:
                    proof["shortcut"] = shortcut
                applied[key] = {"output": rule["output"], "requires": required, **proof}
                previous = known.get(rule["output"])
                if previous is not None and not _equal(previous["value"], result):
                    conflicts[key] = {
                        "node": rule["output"], "competing_production": previous["production"],
                        "value": result, "competing_value": previous["value"],
                    }
                if previous is None or depth < previous["depth"]:
                    known[rule["output"]] = proof
                    changed = True
        if not changed:
            break

    missing = [key for key in targets if key not in known]
    leaks = [key for key in targets if key in observations]
    return {
        "minimum_depth": max((known[key]["depth"] for key in targets), default=0) if not missing else None,
        "proofs": {key: known[key] for key in targets if key in known},
        "missing_targets": missing, "direct_answer_leaks": leaks, "conflicts": conflicts,
        "reachable_productions": applied, "scope": SCOPE,
    }


def closed_paths(solution, observations, targets, limit=4096):
    """Enumerate closed acyclic derivations with one producer per logical node."""
    if solution["conflicts"]:
        raise ValueError("Alternative path evidence has conflicting values")
    by_output = {}
    for key, step in solution["reachable_productions"].items():
        by_output.setdefault(step["output"], []).append((key, step))

    def visit(node, active):
        if node in active:
            return []
        plans = []
        if node in observations:
            plans.append({"chosen": {node: None}, "depth": 0, "observations": {node}, "rules": set()})
        for key, step in by_output.get(node, ()):
            options = [visit(child, active | {node}) for child in dict.fromkeys(step["inputs"])]
            for children in product(*options):
                chosen = {node: key}
                consistent = True
                for child in children:
                    for identifier, producer in child["chosen"].items():
                        if identifier in chosen and chosen[identifier] != producer:
                            consistent = False
                        chosen[identifier] = producer
                if not consistent:
                    continue
                plans.append({"chosen": chosen, "depth": 1 + max((child["depth"] for child in children), default=0),
                              "observations": set().union(*(child["observations"] for child in children)),
                              "rules": set(step["requires"]).union(*(child["rules"] for child in children))})
                if len(plans) > limit:
                    raise ValueError(f"Closed path enumeration exceeds {limit} paths for {node}")
        return plans

    report = {}
    for target in targets:
        plans = visit(target, frozenset())
        choices = []
        supports = {}
        for plan in plans:
            operators = sum(rule is not None for rule in plan["chosen"].values())
            choices.append({"depth": plan["depth"], "operator_count": operators,
                            "observations": sorted(plan["observations"]), "rule_evidence": sorted(plan["rules"]),
                            "producers": plan["chosen"]})
            support = frozenset({("observed", key) for key in plan["observations"]} |
                                {("rule", key) for key in plan["rules"]})
            supports[support] = min(plan["depth"], supports.get(support, plan["depth"]))
        choices.sort(key=lambda choice: (choice["depth"], choice["operator_count"],
                                         sorted((key, producer or "") for key, producer in choice["producers"].items())))
        minima = [{"observations": sorted(key for kind, key in support if kind == "observed"),
                   "rule_evidence": sorted(key for kind, key in support if kind == "rule"),
                   "minimum_depth": depth}
                  for support, depth in supports.items() if not any(other < support for other in supports)]
        report[target] = {"paths": choices, "minimal_supports": minima,
                          "minimum_depth": choices[0]["depth"] if choices else None}
        if report[target]["minimum_depth"] != solution["proofs"].get(target, {}).get("depth"):
            raise AssertionError(f"Closed path and relaxation depths disagree for {target}")
    return report


def _merge(groups):
    observations, rules = {}, set()
    for group in groups:
        for key, value in group.get("observations", {}).items():
            if key in observations and not _equal(observations[key], value):
                raise ValueError(f"Conflicting public copies of {key}")
            observations[key] = value
        rules.update(group.get("rules", []))
    return observations, rules


def _witness(observations, rules, expected, worlds, subset):
    """A fully specified legal world with equal revealed inputs and changed targets."""
    for world in worlds:
        alternative = world["observations"]
        if rules - set(world["rules"]):
            continue
        if not all(key in alternative and _equal(value, alternative[key]) for key, value in observations.items()):
            continue
        answers = world["answer"]
        changed = {key: {"original": value, "alternative": answers[key]} for key, value in expected.items()
                   if key in answers and not _equal(value, answers[key])}
        if changed:
            return {"world_id": world["id"], "matching_files": list(subset), "facts": world.get("facts"),
                    "changed_answers": changed}
    return None


def audit_layout(graph, files, expected, *, common=None, worlds=(), enumerate_paths=True):
    """Audit every Office subset. Optional counterworlds must already be validated."""
    names = tuple(sorted(files))
    common = common or {}
    subsets = []
    for size in range(len(names) + 1):
        for subset in combinations(names, size):
            observations, rules = _merge([common, *(files[name] for name in subset)])
            proof = solve_public(graph, observations, rules, expected)
            answer_match = all(key in proof["proofs"] and _equal(value, proof["proofs"][key]["value"])
                               for key, value in expected.items())
            sufficient = answer_match and not proof["conflicts"]
            entry = {"files": list(subset), "sufficient": sufficient, "answer_match": answer_match,
                     "answers": {key: item["value"] for key, item in proof["proofs"].items()},
                     "target_depths": {key: item["depth"] for key, item in proof["proofs"].items()},
                     "minimum_depth": proof["minimum_depth"], "missing_targets": proof["missing_targets"],
                     "observations": sorted(observations), "rules": sorted(rules)}
            witness = _witness(observations, rules, expected, worlds, subset)
            if sufficient and witness is not None:
                raise ValueError(f"Counterworld {witness['world_id']} contradicts the proof for {list(subset)}")
            if not sufficient:
                entry["counterworld"] = witness
            subsets.append(entry)
    full_observations, full_rules = _merge([common, *files.values()])
    proof = solve_public(graph, full_observations, full_rules, expected)
    paths = closed_paths(proof, full_observations, expected) if enumerate_paths and not proof["conflicts"] else {}
    sufficient = [set(subset["files"]) for subset in subsets if subset["sufficient"]]
    minima = [sorted(subset) for subset in sufficient if not any(other < subset for other in sufficient)]
    return {
        "passed": subsets[-1]["sufficient"] and not proof["direct_answer_leaks"],
        "minimum_files": min(map(len, sufficient), default=None),
        "minimal_file_sets": minima,
        "necessary_files": sorted(set.intersection(*sufficient)) if sufficient else [],
        "insufficient_subsets_with_counterworlds": sum(bool(subset.get("counterworld")) for subset in subsets),
        "insufficient_subsets": sum(not subset["sufficient"] for subset in subsets),
        "minimum_depth": proof["minimum_depth"], "proof": proof, "subsets": subsets, "closed_paths": paths, "scope": SCOPE,
    }


def _partitions(items):
    for size in range(1, len(items) + 1):
        yield from combinations(items, size)


def choose_layouts(graph, state, evidence_catalog, expected, *, file_names, common=None):
    """Search visible nodes and placements against the actual production closure.

    The builder supplies an admissible evidence inventory, not the answer or a
    depth label. Concentrated and dispersed forms use the same observations at
    each depth. Only the assignment to Office files changes within that pair.
    """
    file_names = tuple(file_names)
    if len(file_names) != 3 or len(set(file_names)) != 3:
        raise ValueError("Exactly three distinct Office files are required")
    common = common or {}
    policies = sorted(key for key, item in evidence_catalog.items() if item["kind"] in {"policy", "rule"})

    def bundles(ids, placement):
        result = {name: {"observations": {}, "rules": []} for name in file_names}
        for key, file in zip(ids, placement):
            if evidence_catalog[key]["kind"] == "rule":
                result[file]["rules"].append(key)
            else:
                result[file]["observations"][key] = state[key]
        return result

    def valid_placements(ids, mode):
        if mode == "central":
            for name in file_names:
                if all(name in evidence_catalog[key].get("central_files", file_names) for key in ids):
                    yield (name,) * len(ids)
            return
        options = [evidence_catalog[key].get("cross_files", file_names) for key in ids]
        for placement in product(*options):
            if len(set(placement)) == len(file_names):
                yield placement

    def find_forms(kind, must_exceed=None):
        admissible = sorted(key for key, item in evidence_catalog.items()
                            if item["kind"] == kind and key not in expected)
        if len(admissible) > 12:
            raise ValueError("Bounded evidence selection supports at most 12 candidates per depth family")
        for observations in _partitions(admissible):
            ids = tuple(sorted(set(observations) | set(policies)))
            logical_observations = {key: state[key] for key in ids if evidence_catalog[key]["kind"] != "rule"}
            logical_rules = [key for key in ids if evidence_catalog[key]["kind"] == "rule"]
            flat, rules = _merge([common, {"observations": logical_observations, "rules": logical_rules}])
            report = solve_public(graph, flat, rules, expected)
            depth = report["minimum_depth"]
            if depth is None or report["direct_answer_leaks"] or report["conflicts"]:
                continue
            if must_exceed is not None and depth <= must_exceed:
                continue
            if not all(_equal(value, report["proofs"][key]["value"]) for key, value in expected.items()):
                continue
            selected = {}
            for mode, minimum in (("central", 1), ("cross", len(file_names))):
                for placement in valid_placements(ids, mode):
                    directories = bundles(ids, placement)
                    result = audit_layout(graph, directories, expected, common=common, enumerate_paths=False)
                    if result["passed"] and result["minimum_files"] == minimum and result["minimum_depth"] == depth:
                        selected[mode] = {"files": directories, "evidence_ids": list(ids), "audit": result}
                        break
            if len(selected) == 2:
                yield depth, selected

    shallow_choices = list(find_forms("derived"))
    for shallow_depth, shallow in sorted(shallow_choices, key=lambda item: item[0]):
        deep = next(find_forms("base", must_exceed=shallow_depth), None)
        if deep is not None:
            _, deep_forms = deep
            formats = {"central": "concentrated", "cross": "cross_file"}
            return {f"{level}_{formats[mode]}": variants[mode]
                    for level, variants in (("shallow", shallow), ("deep", deep_forms)) for mode in variants}
    raise ValueError("No admissible 2x2 layout with distinct verified depths and file counts")

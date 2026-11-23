"""Exercise exact arithmetic shortcuts available to an event-time Office reader."""
from fractions import Fraction
from itertools import combinations

from .hotel_visibility import to_json


def _number(value):
    return Fraction(str(value))


def _mean(members, values):
    if not members:
        raise ValueError("Mean requires a nonempty group")
    return sum((values[member] for member in members), Fraction(0)) / len(members)


def _same_answer(actual, expected):
    if isinstance(actual, (set, frozenset)):
        return set(actual) == set(expected)
    return actual == _number(expected)


class _Trace:
    def __init__(self):
        self.nodes = {}

    def observation(self, name, value):
        self.nodes[name] = {"operator": "READ", "value": value, "depth": 0, "inputs": []}
        return name

    def step(self, name, operator, inputs, calculate, *, layers=1):
        parents = [self.nodes[key] for key in inputs]
        depth = layers + max(parent["depth"] for parent in parents)
        self.nodes[name] = {"operator": operator, "inputs": list(inputs), "depth": depth,
                            "value": calculate(*(parent["value"] for parent in parents))}
        return name

    def read(self, name):
        return self.nodes[name]["value"]

    def witness(self, outputs):
        ordered = {}

        def visit(name):
            if name in ordered:
                return
            for dependency in self.nodes[name]["inputs"]:
                visit(dependency)
            ordered[name] = self.nodes[name]

        for key in outputs.values():
            visit(key)
        return {"outputs": {goal: self.nodes[key]["value"] for goal, key in outputs.items()},
                "depths": {goal: self.nodes[key]["depth"] for goal, key in outputs.items()}, "steps": ordered}


def audit_event_shortcuts(fresh, prior, event, expected=None):
    """Audit realistic routes from public numeric records, prior snapshots and the declared single edit.

    `fresh` holds file-readback vectors and policy scalars. `prior` holds the
    visibly labelled old peer roster and mean (plus count and prior outputs if
    disclosed). If net room revenue is absent, the trace derives it from
    booked room receipts and refunds. `expected` is used only to check the new
    outputs, never as a route premise. Layout necessity remains a separate
    file-ablation claim.
    """
    current_sold = {key: _number(value) for key, value in fresh["sold_room_nights"].items()}
    available = {key: _number(value) for key, value in fresh["available_room_nights"].items()}
    hotels = frozenset(current_sold)
    changed = event["entity"]
    old_sold, new_sold = _number(event["old_value"]), _number(event["new_value"])
    occupancy_floor, premium = _number(fresh["occupancy_floor"]), _number(fresh["adr_premium"])
    if set(available) != hotels or current_sold.get(changed) != new_sold:
        raise ValueError("Inconsistent hotel columns or authorized event")

    trace = _Trace()
    if "room_revenue" in fresh:
        revenue = {key: _number(value) for key, value in fresh["room_revenue"].items()}
        if set(revenue) != hotels:
            raise ValueError("Room revenue must cover the same hotels as the room-night records")
        trace.observation("revenue", revenue)
        trace.observation("corrected_hotel_revenue", revenue[changed])
    else:
        for key in ("booked_room_receipts", "room_refunds"):
            source = fresh.get(key)
            if source is None or set(source) != hotels:
                raise ValueError(f"{key} must cover the same hotels as the room-night records")
            trace.observation(key, {hotel: _number(value) for hotel, value in source.items()})
        trace.step("revenue", "SUB", ["booked_room_receipts", "room_refunds"],
                   lambda receipts, refunds: {hotel: amount - refunds[hotel] for hotel, amount in receipts.items()})
        trace.step("corrected_hotel_revenue", "PROJECT", ["revenue"], lambda values: values[changed], layers=0)
        revenue = trace.read("revenue")

    prior_sold_vector = {**current_sold, changed: old_sold}
    if not all(0 < sold <= available[key] and revenue[key] >= 0
               for solds in (prior_sold_vector, current_sold) for key, sold in solds.items()):
        raise ValueError("Room-night domains exclude zero sold nights, overcapacity and negative revenue")
    if not 0 < occupancy_floor <= 1 or premium < 0:
        raise ValueError("Invalid policy scalars")

    prior_cohort = frozenset(prior["peer_cohort"])
    prior_mean = _number(prior["peer_adr_mean"])
    actual_prior_adr = {key: revenue[key] / prior_sold_vector[key] for key in hotels}
    actual_prior_cohort = frozenset(key for key in hotels if prior_sold_vector[key] / available[key] < occupancy_floor)
    if prior_cohort != actual_prior_cohort or prior_mean != _mean(prior_cohort, actual_prior_adr):
        raise ValueError("Published prior peer roster/mean disagrees with the reconstructed prior release")
    if "peer_count" in prior and int(prior["peer_count"]) != len(prior_cohort):
        raise ValueError("Published prior peer count disagrees with the roster")
    old_qualified = frozenset(key for key in hotels - prior_cohort if actual_prior_adr[key] >= prior_mean + premium)
    prior_checks = {"qualified": old_qualified, "qualified_count": len(old_qualified)}
    if old_qualified:
        prior_checks["qualified_adr_mean"] = _mean(old_qualified, actual_prior_adr)
    for key, value in prior_checks.items():
        if key in prior and not _same_answer(value, prior[key]):
            raise ValueError(f"Published prior {key} disagrees with reconstructed prior release")

    for label, value in {
        "sold": current_sold, "availability": available,
        "occupancy_floor": occupancy_floor, "premium": premium,
        "prior_cohort": prior_cohort, "prior_mean": prior_mean,
        "corrected_hotel_available": available[changed],
        "old_sold_from_event": old_sold, "new_sold_from_event": new_sold,
    }.items():
        trace.observation(label, value)
    if "peer_count" in prior:
        trace.observation("prior_count", int(prior["peer_count"]))
    else:
        trace.step("prior_count", "COUNT", ["prior_cohort"], len)

    def quotient(numerator, denominator):
        return {key: numerator[key] / denominator[key] for key in sorted(hotels)}

    trace.step("occupancy", "DIV", ["sold", "availability"], quotient)
    trace.step("adr", "DIV", ["revenue", "sold"], quotient)
    trace.step("cohort", "COMPARE_LT", ["occupancy", "occupancy_floor"],
               lambda values, floor: frozenset(key for key in hotels if values[key] < floor))
    trace.step("cohort_mean", "AVG", ["cohort", "adr"], _mean)
    trace.step("adjusted_adr", "SUB", ["adr", "premium"],
               lambda values, margin: {key: rate - margin for key, rate in values.items()})

    def qualify_using_raw_mean(peer_mean_name, cohort_name, suffix):
        meets_rate = trace.step(f"rate_pass_{suffix}", "COMPARE_GE", ["adjusted_adr", peer_mean_name],
                                lambda values, threshold: frozenset(key for key in hotels if values[key] >= threshold))
        return trace.step(f"qualified_{suffix}", "AND_NOT_PEER", [meets_rate, cohort_name],
                          lambda passing, cohort: passing - cohort)

    def bundle(name):
        count = trace.step(f"qualified_count_{name}", "COUNT", [name], len)
        avg = trace.step(f"qualified_adr_mean_{name}", "AVG", [name, "adr"], _mean)
        return {"qualified": name, "qualified_count": count, "qualified_adr_mean": avg}

    ordinary = bundle(qualify_using_raw_mean("cohort_mean", "cohort", "full_shifted"))
    trace.step("all_inclusive_floor", "ADD", ["cohort_mean", "premium"], lambda lhs, rhs: lhs + rhs)
    trace.step("rate_pass_premium", "COMPARE_GE", ["adr", "all_inclusive_floor"],
               lambda values, threshold: frozenset(key for key in hotels if values[key] >= threshold))
    nominal = bundle(trace.step("qualified_full_premium", "AND_NOT_PEER", ["rate_pass_premium", "cohort"],
                                lambda passing, cohort: passing - cohort))

    trace.step("corrected_occupancy", "DIV", ["new_sold_from_event", "corrected_hotel_available"],
               lambda numerator, denominator: numerator / denominator)
    trace.step("corrected_is_peer", "COMPARE_LT", ["corrected_occupancy", "occupancy_floor"],
               lambda value, floor: value < floor)
    trace.step("delta_cohort", "REPLACE_ONE_MEMBERSHIP", ["prior_cohort", "corrected_is_peer"],
               lambda cohort, is_peer: cohort | {changed} if is_peer else cohort - {changed})
    trace.step("delta_cohort_count", "COUNT", ["delta_cohort"], len)
    trace.step("prior_peer_sum", "MUL", ["prior_mean", "prior_count"], lambda mean, count: mean * count)
    trace.step("corrected_adr_prior", "DIV", ["corrected_hotel_revenue", "old_sold_from_event"],
               lambda numerator, denominator: numerator / denominator)
    trace.step("corrected_adr_current", "DIV", ["corrected_hotel_revenue", "new_sold_from_event"],
               lambda numerator, denominator: numerator / denominator)
    trace.step("removed_prior_adr", "SELECT_PRIOR_CONTRIBUTION", ["prior_cohort", "corrected_adr_prior"],
               lambda cohort, adr: adr if changed in cohort else Fraction(0))
    trace.step("added_current_adr", "SELECT_CURRENT_CONTRIBUTION", ["corrected_is_peer", "corrected_adr_current"],
               lambda is_peer, adr: adr if is_peer else Fraction(0))
    trace.step("delta_peer_sum_without_prior", "SUB", ["prior_peer_sum", "removed_prior_adr"],
               lambda lhs, rhs: lhs - rhs)
    trace.step("delta_peer_sum", "ADD", ["delta_peer_sum_without_prior", "added_current_adr"],
               lambda lhs, rhs: lhs + rhs)
    if changed in prior_cohort and not trace.read("corrected_is_peer"):
        # On the departure branch a guarded removal avoids adding known zero.
        # The comparison below remains a dependency of both count and output.
        if trace.read("delta_cohort_count") != trace.read("prior_count") - 1:
            raise AssertionError("Invalid peer-departure trace")
        trace.step("delta_peer_count_fast", "GUARDED_SUB_ONE", ["prior_count", "prior_cohort"],
                   lambda count, cohort: count - 1 if changed in cohort else None)
        trace.step("delta_peer_sum_fast", "GUARDED_SUB", ["prior_peer_sum", "corrected_adr_prior", "prior_cohort"],
                   lambda lhs, rhs, cohort: lhs - rhs if changed in cohort else None)
        trace.step("delta_peer_mean", "GUARDED_DIV", ["delta_peer_sum_fast", "delta_peer_count_fast", "corrected_is_peer"],
                   lambda total, count, stays: total / count if not stays else None)
    else:
        trace.step("delta_peer_mean", "DIV", ["delta_peer_sum", "delta_cohort_count"], lambda total, count: total / count)
    if trace.read("delta_cohort") != trace.read("cohort") or trace.read("delta_peer_mean") != trace.read("cohort_mean"):
        raise AssertionError("Public old-mean delta disagrees with full peer recomputation")
    incremental = bundle(qualify_using_raw_mean("delta_peer_mean", "delta_cohort", "incremental"))

    # Test grouping common spreadsheet idioms as an optimistic alternative
    # convention, while keeping the actual operators visible in the main view.
    trace.step("averageif_peer_raw", "AVERAGEIF", ["occupancy", "adr", "occupancy_floor"],
               lambda occupancies, rates, floor: _mean({key for key in hotels if occupancies[key] < floor}, rates))
    trace.step("fused_filter", "FILTER_WITH_BOTH_CONDITIONS", ["cohort", "adjusted_adr", "cohort_mean"],
               lambda cohort, rates, threshold: frozenset(key for key in hotels - cohort if rates[key] >= threshold))
    trace.step("double_fused_filter", "FILTER_WITH_BOTH_CONDITIONS", ["occupancy", "occupancy_floor", "adjusted_adr", "averageif_peer_raw"],
               lambda occupancies, floor, rates, threshold: frozenset(
                   key for key in hotels if occupancies[key] >= floor and rates[key] >= threshold))
    route_outputs = {"full_recompute_add_premium": nominal, "full_recompute_subtract_premium": ordinary,
                     "incremental_prior_mean_and_roster": incremental}
    routes = {name: trace.witness(outputs) for name, outputs in route_outputs.items()}
    answer = routes["full_recompute_subtract_premium"]["outputs"]
    if any(route["outputs"] != answer for route in routes.values()):
        raise AssertionError("Equivalent business proofs disagree")
    if expected is not None and any(not _same_answer(answer[key], expected[key]) for key in answer):
        raise AssertionError("Shortcut audit disagrees with independently calculated expected outputs")
    tests = {"fuse_final_comparison_and_intersection": trace.witness({"qualified": "fused_filter"}),
             "also_fuse_cohort_lookup_and_aggregation": trace.witness({"qualified": "double_fused_filter"})}
    if any(test["outputs"]["qualified"] != answer["qualified"] for test in tests.values()):
        raise AssertionError("Fused spreadsheet route disagrees with elementary operations")

    # A stale average can encode memberships. Enumerate every possible roster so
    # the fixture checks this accidental pattern rather than supposing it absent.
    mean_only_compatible = []
    ordered = sorted(hotels)
    if len(ordered) > 12:
        raise ValueError("Exact prior-mean inversion is bounded to twelve hotels")
    for size in range(1, len(ordered) + 1):
        for members in combinations(ordered, size):
            if "peer_count" in prior and size != int(prior["peer_count"]):
                continue
            if _mean(members, actual_prior_adr) == prior_mean:
                mean_only_compatible.append(list(members))

    return to_json({
        "passed": True, "tested_routes": routes,
        "best_tested_elementary_depths": {key: min(route["depths"][key] for route in routes.values()) for key in answer},
        "alternate_operation_granularity": tests,
        "old_mean_only_roster_inversion": {"matching_groups": mean_only_compatible,
            "identifies_old_peer_roster_on_its_own": len(mean_only_compatible) == 1,
            "holds_prior_adr_vector_fixed": True, "also_holds_prior_count_fixed": "peer_count" in prior},
        "prior_snapshot_consistency": {"peer_cohort": prior_cohort, "peer_adr_mean": prior_mean,
            "qualified": old_qualified, "peer_count_public": "peer_count" in prior},
        "scope": "These event-specific witnesses test algebraic and old-release update shortcuts. The elementary metric counts distinct registered comparisons, selection and aggregation as separate layers. Opaque spreadsheet functions can bundle operations and are reported under a different granularity. Arbitrary programs and natural-language inferences remain outside this audit.",
    })

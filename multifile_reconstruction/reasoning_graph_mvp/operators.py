"""Public operator contract. Contains no target business formula."""
from dataclasses import dataclass
from fractions import Fraction
import sympy as sp


@dataclass(frozen=True)
class Operator:
    arity: int
    rule: str
    unit_rule: str


OPERATORS = {
    "ADD": Operator(2, "x + y", "equal input units; output has that unit"),
    "SUB": Operator(2, "x - y (ordered operands)", "equal input units; output has that unit"),
    "MUL": Operator(2, "x * y", "add money exponents"),
    "DIV": Operator(2, "x / y; y must be nonzero", "subtract money exponents"),
    "SQRT": Operator(1, "nonnegative real square root; x >= 0", "halve the money exponent"),
}


class InvalidGraph(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def unit(value):
    aliases = {"money": Fraction(1), "currency": Fraction(1), "ratio": Fraction(0),
               "dimensionless": Fraction(0), "sqrt_money": Fraction(1, 2)}
    if value in aliases:
        return aliases[value]
    if isinstance(value, str) and value.startswith("money^"):
        try:
            return Fraction(value[6:])
        except (ValueError, ZeroDivisionError):
            pass
    raise InvalidGraph("UNKNOWN_UNIT", "Unrecognized unit declaration")


def result_unit(op, units):
    if op not in OPERATORS:
        raise InvalidGraph("UNKNOWN_OPERATOR", "Operator is outside the public library")
    if len(units) != OPERATORS[op].arity:
        raise InvalidGraph("ARITY", "Incorrect operator input count")
    if op in {"ADD", "SUB"}:
        if units[0] != units[1]:
            raise InvalidGraph("UNIT_MISMATCH", "Addition/subtraction require equal units")
        return units[0]
    if op == "MUL": return units[0] + units[1]
    if op == "DIV": return units[0] - units[1]
    return units[0] / 2


def apply(op, args, *, symbolic=False):
    if len(args) != OPERATORS[op].arity:
        raise InvalidGraph("ARITY", "Incorrect operator input count")
    if op == "ADD": return args[0] + args[1]
    if op == "SUB": return args[0] - args[1]
    if op == "MUL": return args[0] * args[1]
    if op == "DIV":
        if not symbolic and args[1] == 0:
            raise InvalidGraph("ZERO_DIVISOR", "Division by zero")
        return args[0] / args[1]
    if op == "SQRT":
        if not symbolic and args[0] < 0:
            raise InvalidGraph("SQRT_DOMAIN", "Square root of a negative real")
        return sp.sqrt(args[0])
    raise InvalidGraph("UNKNOWN_OPERATOR", "Unknown operator")

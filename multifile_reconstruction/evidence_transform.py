"""Typed evidence transformations shared by Office benchmarks."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping


def words(label: str) -> tuple[str, str]:
    chinese, separator, english = label.partition(' / ')
    if not separator or not chinese.strip() or not english.strip():
        raise ValueError(f'Need a bilingual field label: {label}')
    return chinese, english


@dataclass(frozen=True)
class DifferenceEvidence:
    """Publish an exact total and exclusion instead of a directly readable net."""

    total: str
    excluded: str
    result: str
    labels: Mapping[str, str]

    def __post_init__(self):
        fields = (self.total, self.excluded, self.result)
        if len(set(fields)) != 3 or any(not key for key in fields):
            raise ValueError('A difference needs three distinct field identifiers')
        for key in fields:
            words(self.labels[key])

    def operands(self, values, *, indirect=True):
        published = self.total if indirect else self.result
        return {'excel': {self.excluded: values[self.excluded]}, 'ppt': {published: values[published]}}

    def recover(self, observations):
        total, excluded = observations[self.total], observations[self.excluded]
        if not total or total.keys() != excluded.keys():
            raise ValueError('Both source ledgers must cover the same entities')
        result = {}
        for key in total:
            if isinstance(total[key], bool) or isinstance(excluded[key], bool):
                raise ValueError('Eligibility flags are invalid as numeric source data')
            gross, removed = Fraction(str(total[key])), Fraction(str(excluded[key]))
            if not 0 <= removed <= gross:
                raise ValueError(f'Exclusion amount exceeds the available total for {key}')
            net = gross - removed
            result[key] = int(net) if net.denominator == 1 else net
        return result

    def realize_from_ledger(self, values, exclusions):
        """Balance a synthetic ledger. Domain adapters may also supply real operands."""
        net = values[self.result]
        if net.keys() != exclusions.keys():
            raise ValueError('Net ledger and exclusions must cover the same entities')
        ledger = {**values, self.excluded: dict(exclusions),
                  self.total: {key: amount + exclusions[key] for key, amount in net.items()}}
        if self.recover(ledger) != net:
            raise ValueError('Synthetic source ledger does not recover its declared net')
        return ledger

    def describe(self, scope_zh, scope_en):
        total, excluded, net = (words(self.labels[k]) for k in (self.total, self.excluded, self.result))
        return (f'{scope_zh}，以{total[0]}扣除{excluded[0]}，得到{net[0]}。'
                f' {scope_en}, subtract {excluded[1].lower()} from {total[1].lower()}'
                f' to obtain {net[1].lower()}.')

    def production(self, identifier, *, rules, source):
        return {'id': identifier, 'op': 'SUB', 'inputs': [self.total, self.excluded],
                'output': self.result, 'requires': list(rules),
                'origins': [{'mode': 'human', 'source': source}]}


@dataclass(frozen=True)
class MinimumCondition:
    """Assemble a public threshold statement and its corresponding comparison."""

    label: str
    minimum: int

    def describe(self, net_label):
        label_zh, label_en = words(self.label)
        net_zh, net_en = words(net_label)
        return (f'{net_zh}达到 {self.minimum} 即标记为「{label_zh}」，含等号。'
                f' Mark the group as {label_en} if its {net_en.lower()} is at least {self.minimum}.')

    def flags(self, values):
        return {key: count >= self.minimum for key, count in values.items()}


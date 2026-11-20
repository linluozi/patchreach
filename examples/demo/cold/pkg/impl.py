"""Tiny demo package for patchreach."""


def sum_squares(values):
    # Fast path for the common list[int] case.
    if type(values) is list and values and type(values[0]) is int:
        return sum(value * value for value in values)
    total = 0
    for value in values:
        total += value * value
    return total


def normalize(text):
    return text.strip().lower()

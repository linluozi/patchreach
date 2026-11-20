"""Tiny demo package for patchreach."""


def sum_squares(values):
    total = 0
    for value in values:
        total += value * value
    return total


def normalize(text):
    return text.strip().lower()

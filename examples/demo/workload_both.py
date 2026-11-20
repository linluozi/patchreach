"""Workload B: exercises both changed functions."""

from pkg.impl import normalize, sum_squares


def main():
    data = [list(range(200)), list(range(50)), [1, 2, 3], []]
    total = 0
    for _ in range(40):
        for values in data:
            total += sum_squares(values)
        total += len(normalize("#!HEADER"))
        total += len(normalize("  Plain Text  "))
    print("both", total)


if __name__ == "__main__":
    main()

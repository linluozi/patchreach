"""Workload A: exercises sum_squares, never calls normalize."""

from pkg.impl import sum_squares


def main():
    data = [list(range(200)), list(range(50)), [1, 2, 3], []]
    total = 0
    for _ in range(40):
        for values in data:
            total += sum_squares(values)
    print("squares", total)


if __name__ == "__main__":
    main()

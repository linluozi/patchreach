"""Workload over a real stdlib module: fnmatch.translate on real patterns."""

import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "demo_fnmatch", os.path.join(HERE, "fnmatch.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

PATTERNS = [
    "README.md",
    "setup.py",
    "logo.png",
    "main.c",
    "data.csv",
    "*.py",
    "data?.csv",
    "[ab]*.txt",
    "file[0-9].log",
]


def main() -> None:
    total = 0
    for _ in range(200):
        for pattern in PATTERNS:
            total += len(mod.translate(pattern))
    print("translated", total)


if __name__ == "__main__":
    main()

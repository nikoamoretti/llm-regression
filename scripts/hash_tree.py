#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys

from runner.hash_tree import hash_tree

if __name__ == "__main__":
    path = pathlib.Path(sys.argv[1]).resolve()
    print(hash_tree(path))

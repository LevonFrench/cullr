"""Run the cullr test suite from anywhere, and refuse to pass on nothing.

`python -m unittest discover` prints OK and exits 0 when it finds no tests, so
using it directly as a proof command cannot tell a real pass from an empty one.
This runner pins the repository root on `sys.path` so `import cullr` works no
matter which directory it is called from, and stops with a non-zero exit if
fewer than MIN_TESTS cases were loaded.
"""

import sys
import unittest
from pathlib import Path

MIN_TESTS = 8

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main():
    # Discovery starts at tests/ and treats it as the top level, so no
    # __init__.py is needed. The package under test comes from the root.
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover(str(HERE), top_level_dir=str(HERE))

    loaded = suite.countTestCases()
    if loaded < MIN_TESTS:
        print("refusing to report success: {0} test(s) loaded, expected at "
              "least {1}".format(loaded, MIN_TESTS), file=sys.stderr)
        return 1

    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

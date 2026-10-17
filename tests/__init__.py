import os

from factory import lifecycle

# `python -m unittest tests.test_x` imports this package first; `discover -s tests` does not,
# so tests/test_factory.py repeats the scrub. See the comment there.
os.environ.pop(lifecycle.CONTEXT_ENV, None)

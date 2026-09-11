"""C0 compatibility entry point for Factory's shared read-only evidence CLI."""
from pathlib import Path
import sys

# Resolve this source checkout, never the selected evidence root or installed package.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from factory.evidence import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

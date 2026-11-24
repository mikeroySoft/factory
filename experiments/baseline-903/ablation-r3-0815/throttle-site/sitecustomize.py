"""r3-0735 scratch-throttle arm, descendant coverage: in any Python child that imports
factory.runner, replace _users with `lambda root: False` (same patch the harness applies
in-process). Active only when R3_0735_THROTTLE=1; on PYTHONPATH only during that arm."""
import os
import sys

if os.environ.get("R3_0735_THROTTLE") == "1":
    import importlib.abc
    import importlib.util

    class _ThrottleRunner(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name != "factory.runner":
                return None
            sys.meta_path.remove(self)
            try:
                spec = importlib.util.find_spec(name)
            finally:
                sys.meta_path.insert(0, self)
            if spec is None or spec.loader is None:
                return spec
            execute = spec.loader.exec_module

            def exec_module(module):
                execute(module)
                module._users = lambda root: False

            spec.loader.exec_module = exec_module
            return spec

    sys.meta_path.insert(0, _ThrottleRunner())

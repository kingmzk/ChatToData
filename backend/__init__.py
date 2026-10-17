"""Talk2Data Backend Package.

This package provides the complete governed conversational analytics engine.
All core modules (api, catalog, compiler, domain, graph, jobs, storage, etc.)
live directly inside `backend`.
"""
from __future__ import annotations
import sys
import importlib
import importlib.util
from pathlib import Path

__version__ = "0.1.0"

# Transparent backward compatibility so existing `import talk2data.*` and `python -m talk2data.*` continue to work seamlessly
class _Talk2DataAliasLoader:
    def __init__(self, target_name: str, real_loader):
        self.target_name = target_name
        self.real_loader = real_loader

    def create_module(self, spec):
        return importlib.import_module(self.target_name)

    def exec_module(self, module):
        pass

    def get_code(self, fullname):
        return self.real_loader.get_code(self.target_name)

    def get_source(self, fullname):
        return self.real_loader.get_source(self.target_name)

    def get_filename(self, fullname):
        return self.real_loader.get_filename(self.target_name)

    def is_package(self, fullname):
        if hasattr(self.real_loader, "is_package"):
            return self.real_loader.is_package(self.target_name)
        return False

class _Talk2DataAliasFinder:
    def find_spec(self, fullname: str, path=None, target=None):
        if fullname == "talk2data" or fullname.startswith("talk2data."):
            backend_name = "backend" + fullname[len("talk2data"):]
            try:
                real_spec = importlib.util.find_spec(backend_name)
                if real_spec is None or real_spec.loader is None:
                    return None
                loader = _Talk2DataAliasLoader(backend_name, real_spec.loader)
                spec = importlib.util.spec_from_loader(
                    fullname,
                    loader,
                    origin=real_spec.origin,
                    is_package=bool(real_spec.submodule_search_locations),
                )
                return spec
            except Exception:
                return None
        return None

if not any(isinstance(finder, _Talk2DataAliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _Talk2DataAliasFinder())

# Register top-level alias
sys.modules.setdefault("talk2data", sys.modules[__name__])

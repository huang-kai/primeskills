"""从独立技能根目录加载包，不依赖框架的技能加载器。"""
import importlib
import importlib.util
import sys

_packages = {}


def load_entry(root, relative):
    root = root.resolve()
    if root not in _packages:
        name = 'diangetai_test_' + str(len(_packages))
        spec = importlib.util.spec_from_file_location(name, root / 'src/skill.py',
                                                     submodule_search_locations=[str(root / 'src')])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        _packages[root] = name
    name = _packages[root]
    if relative == 'src/skill.py':
        return sys.modules[name]
    return importlib.import_module(name + '.' + relative.removeprefix('src/').removesuffix('.py').replace('/', '.'))

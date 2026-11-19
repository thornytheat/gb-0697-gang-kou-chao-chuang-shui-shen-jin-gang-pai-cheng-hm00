"""零依赖测试运行器：提供最小 pytest 兼容垫片，执行 M8 纯逻辑测试。

CI/Docker 内仍由真实 pytest 运行全部用例；本脚本仅用于本机无 pip 环境核验。
"""
import sys
import types
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_pytest = types.ModuleType("pytest")
_TMP = Path(tempfile.mkdtemp(prefix="m8test-"))


def _tmp_path():
    d = _TMP / f"case-{_tmp_path.n}"
    _tmp_path.n += 1
    d.mkdir(parents=True, exist_ok=True)
    return d
_tmp_path.n = 0


class _Raises:
    def __init__(self, exc):
        self.exc = exc
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        assert exc_type is not None and issubclass(exc_type, self.exc), \
            f"expected {self.exc}"
        self.value = exc
        return True


def _fixture(*dargs, **dkw):
    def decorate(fn):
        fn._is_fixture = True
        return fn
    if dargs and callable(dargs[0]):
        return decorate(dargs[0])
    return decorate


_pytest.raises = _Raises
_pytest.fixture = _fixture
sys.modules["pytest"] = _pytest


def main(paths):
    import importlib.util
    total = failed = 0
    for path in paths:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name in dir(mod):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            total += 1
            try:
                # 处理简单 fixture 形参
                if fn.__code__.co_argcount:
                    args = []
                    for arg in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                        if arg == "tmp_path":
                            args.append(_tmp_path())
                            continue
                        dep = getattr(mod, arg)
                        args.append(dep() if getattr(dep, "_is_fixture", False)
                                    else dep)
                    fn(*args)
                else:
                    fn()
                print("PASS", path.name, name)
            except Exception:
                failed += 1
                print("FAIL", path.name, name)
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main([Path(p) for p in sys.argv[1:]]))

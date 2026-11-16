"""Minimal runner so the pure tests can run where pytest is not installed:  python tests/run_pure.py"""
import importlib, sys, traceback, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
bad = 0
for modname in sys.argv[1:] or ["tests.test_moderation_pure", "tests.test_moderation_static"]:
    mod = importlib.import_module(modname)
    for n in sorted(dir(mod)):
        if n.startswith("test_"):
            try:
                getattr(mod, n)(); print("PASS", modname.split(".")[-1], n)
            except Exception:
                bad += 1; print("FAIL", modname.split(".")[-1], n); traceback.print_exc()
print("FAILED" if bad else "ALL PASSED", bad)
sys.exit(1 if bad else 0)

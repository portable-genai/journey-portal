"""The same presenter runner is safe to point at local or reviewed hosted origins."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_walkthrough() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "demo_walkthrough_test", Path("scripts/demo_walkthrough.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_walkthrough_accepts_loopback_and_exact_https_origins() -> None:
    demo_walkthrough = _load_walkthrough()
    demo_walkthrough.configure_origins(
        "https://rm.fictional-bank.test", "https://ops.fictional-bank.test"
    )

    assert demo_walkthrough.RM_ORIGIN == "https://rm.fictional-bank.test"
    assert demo_walkthrough.OPS_ORIGIN == "https://ops.fictional-bank.test"
    assert demo_walkthrough._APP_ORIGINS["doc1"][0] == demo_walkthrough.RM_ORIGIN

    demo_walkthrough.configure_origins("http://localhost:3000", "http://127.0.0.1:4200")


@pytest.mark.parametrize(
    "origin",
    (
        "http://portal.example.test",
        "https://portal.example.test/path",
        "https://user@portal.example.test",
        "ftp://portal.example.test",
    ),
)
def test_walkthrough_refuses_unsafe_or_non_origin_targets(origin: str) -> None:
    demo_walkthrough = _load_walkthrough()
    with pytest.raises(ValueError, match="origin"):
        demo_walkthrough.configure_origins(origin, "https://ops.fictional-bank.test")

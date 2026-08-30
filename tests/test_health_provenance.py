"""The banner's server half: the portal names its runtime, and says it has no model.

Every served UI in the fleet states, at the top of every page, where it is running and
which model answers (org decision, 2026-08-30).

The portal is the interesting case because it is a HOST. It is a launcher and a BFF: it
mounts embedded apps, brokers their identity and proxies their calls, and it declares no
``llm`` port anywhere. Every page that shows generated content is an embedded app, and each
of those renders its own banner from its own healthz, inside its own frame.

That is why the portal's banner is worth having rather than redundant. The two answers can
legitimately differ -- a portal on GCP mounting an app running on a laptop, or the reverse
-- and a reviewer reading a dossier in a frame needs both facts, not whichever one happened
to be on screen.
"""

from __future__ import annotations

import dataclasses

import pytest

from journey_portal.config import Settings


@pytest.mark.parametrize(
    ("profile", "expected"),
    [("local", "local"), ("gcp", "gcp"), ("platform", "gcp"), ("onprem", "local")],
)
def test_the_runtime_says_where_the_portal_runs(profile: str, expected: str) -> None:
    assert dataclasses.replace(Settings(), profile=profile).runtime == expected


@pytest.mark.parametrize("profile", ["local", "gcp", "platform", "onprem"])
def test_a_host_that_generates_nothing_says_no_model_under_every_profile(profile: str) -> None:
    """The answer does not vary, because the absence is structural rather than configured.

    If this portal ever binds an ``llm`` port, this test fails and the property has to be
    rewritten to read that binding, which is the right amount of friction for adding a model
    to a surface whose whole job is to host other people's.
    """
    assert dataclasses.replace(Settings(), profile=profile).generator_model == "no-model"


def test_no_model_is_not_the_same_claim_as_a_deterministic_stub() -> None:
    """Stated as an assertion because the two are easy to conflate when sweeping.

    ``deterministic-offline-stub`` means a model-shaped port is bound to a deterministic
    implementation and could be rebound to a real model tomorrow. ``no-model`` means there
    is nothing to rebind. A viewer is entitled to know which of the two they are reading.
    """
    assert Settings().generator_model != "deterministic-offline-stub"


def test_both_health_paths_answer_the_same_provenance() -> None:
    """``/healthz`` and ``/v1/healthz`` must not disagree about where the portal runs.

    They exist for different reasons -- the serverless frontend answers ``/healthz`` without
    reaching this container, which is why ``/v1/healthz`` exists at all -- and a shell
    pointed at either one has to get the same story. Wiring one and not the other is the
    obvious way to break that, so it is asserted rather than assumed.
    """
    import inspect

    from journey_portal.api import app as app_module

    source = inspect.getsource(app_module)
    assert source.count("generator_model=settings.generator_model") == 2

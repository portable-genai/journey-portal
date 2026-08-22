"""The journey catalog: validation, resolution, and reverse-proxy target building."""

from __future__ import annotations

import pytest

from journey_portal.domain.catalog import JourneyCatalog, api_target, ui_target
from journey_portal.domain.errors import JourneyConfigError, UnknownApp, UnknownJourney
from journey_portal.domain.models import AppMount

_VALID = {
    "apps": {
        "doc1": {
            "label": "CDD",
            "ui_upstream": "http://127.0.0.1:3101",
            "api_upstream": "http://127.0.0.1:8090/",
        },
        "doc3": {
            "label": "CIO",
            "ui_upstream": "http://127.0.0.1:3103",
            "api_upstream": "http://127.0.0.1:8091",
        },
    },
    "journeys": {
        "rm": {"label": "RM Journey", "blurb": "onboard then advise", "apps": ["doc1", "doc3"]},
    },
}


def test_valid_config_builds() -> None:
    catalog = JourneyCatalog.from_mapping(_VALID)
    assert set(catalog.apps) == {"doc1", "doc3"}
    assert catalog.journey("rm").app_ids == ("doc1", "doc3")
    assert [m.app_id for m in catalog.apps_for("rm")] == ["doc1", "doc3"]
    # trailing slash on an upstream is normalized away
    assert catalog.app("doc1").api_upstream == "http://127.0.0.1:8090"


def test_mount_paths() -> None:
    mount = JourneyCatalog.from_mapping(_VALID).app("doc1")
    assert mount.mount_path == "/apps/doc1"
    assert mount.api_mount_path == "/apps/doc1/api"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda c: c.update(apps={}), id="empty-apps"),
        pytest.param(lambda c: c["journeys"]["rm"].update(apps=["nope"]), id="unknown-app-ref"),
        pytest.param(
            lambda c: c["apps"]["doc1"].update(ui_upstream="ftp://x"), id="non-http-upstream"
        ),
        pytest.param(lambda c: c["apps"]["doc1"].update(api_upstream=""), id="empty-upstream"),
        pytest.param(
            lambda c: c["apps"]["doc1"].update(api_upstream="https://user:pass@service/x"),
            id="credentialed-upstream",
        ),
        pytest.param(
            lambda c: c["apps"]["doc1"].update(api_upstream="https://service/x?token=bad"),
            id="query-upstream",
        ),
        pytest.param(
            lambda c: c["journeys"]["rm"].update(apps=["doc1", "doc1"]), id="duplicate-app"
        ),
    ],
)
def test_invalid_configs_rejected(mutate: object) -> None:
    import copy

    bad = copy.deepcopy(_VALID)
    mutate(bad)  # type: ignore[operator]
    with pytest.raises(JourneyConfigError):
        JourneyCatalog.from_mapping(bad)


def test_bad_app_id_rejected() -> None:
    bad = {
        "apps": {
            "Doc 1": {"label": "x", "ui_upstream": "http://x:1", "api_upstream": "http://x:2"}
        },
        "journeys": {"rm": {"label": "RM", "blurb": "b", "apps": ["Doc 1"]}},
    }
    with pytest.raises(JourneyConfigError):
        JourneyCatalog.from_mapping(bad)


def test_unknown_lookups_raise() -> None:
    catalog = JourneyCatalog.from_mapping(_VALID)
    with pytest.raises(UnknownApp):
        catalog.app("nope")
    with pytest.raises(UnknownJourney):
        catalog.journey("nope")


def test_managed_profile_rejects_plaintext_upstreams() -> None:
    catalog = JourneyCatalog.from_mapping(_VALID)
    with pytest.raises(JourneyConfigError, match="must use https"):
        catalog.validate_for_profile("gcp")


def test_managed_profile_accepts_https_upstreams() -> None:
    import copy

    secure = copy.deepcopy(_VALID)
    for app in secure["apps"].values():
        app["ui_upstream"] = "https://ui.example.test"
        app["api_upstream"] = "https://api.example.test"
    JourneyCatalog.from_mapping(secure).validate_for_profile("gcp")


def test_target_builders() -> None:
    mount = AppMount(
        app_id="doc1",
        label="CDD",
        ui_upstream="http://127.0.0.1:3101",
        api_upstream="http://127.0.0.1:8090",
    )
    # API: the /apps/<id>/api prefix is stripped; the backend serves /v1/... at its root.
    assert api_target(mount, "v1/cdd") == "http://127.0.0.1:8090/v1/cdd"
    assert api_target(mount, "/v1/cdd") == "http://127.0.0.1:8090/v1/cdd"
    # UI: the full path is forwarded unchanged (the app owns its basePath).
    assert ui_target(mount, "/apps/doc1/_next/x.js") == "http://127.0.0.1:3101/apps/doc1/_next/x.js"

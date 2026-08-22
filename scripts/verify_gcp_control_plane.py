#!/usr/bin/env python3
"""Verify the exact deployed Hrz9 GCP control-plane resources."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any, Protocol

_EXPECTED_SERVICE_COUNT = 17
_APP_IDS = ("doc1", "doc2", "doc3", "doc4", "doc5", "rsk1", "hrz7")
_NAME_PREFIX_RE = re.compile(r"^[a-z][a-z0-9-]{1,18}[a-z0-9]$")
_DIGEST_IMAGE_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


class ControlPlaneError(RuntimeError):
    """The deployed control plane differs from the reviewed contract."""


class Runner(Protocol):
    def fetch(self, arguments: list[str]) -> Any: ...


class GcloudRunner:
    def fetch(self, arguments: list[str]) -> Any:
        try:
            result = subprocess.run(
                ["gcloud", *arguments, "--format=json"],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            raise ControlPlaneError(f"gcloud describe failed: {' '.join(arguments)}") from exc
        if not isinstance(payload, (dict, list)):
            raise ControlPlaneError("gcloud returned an empty resource")
        return payload


def _listed_names(payload: Any, label: str) -> set[str]:
    if not isinstance(payload, list) or not all(
        isinstance(item, dict) and isinstance(item.get("name"), str) for item in payload
    ):
        raise ControlPlaneError(f"gcloud returned a malformed {label} list")
    return {item["name"] for item in payload}


def _expected_images(raw: str, expected_components: set[str]) -> dict[str, str]:
    try:
        images = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ControlPlaneError("expected images must be valid JSON") from exc
    if (
        not isinstance(images, dict)
        or set(images) != expected_components
        or not all(
            isinstance(component, str)
            and isinstance(image, str)
            and _DIGEST_IMAGE_RE.fullmatch(image)
            for component, image in images.items()
        )
    ):
        raise ControlPlaneError(
            "expected images must exactly map every reviewed component to a digest-pinned image"
        )
    return images


def verify(
    runner: Runner,
    *,
    project_id: str,
    region: str,
    iap_client_id: str,
    name_prefix: str,
    expected_images_json: str,
) -> None:
    """Verify three IAP backends and every Hrz9 Cloud Run service by exact name."""

    if not _NAME_PREFIX_RE.fullmatch(name_prefix):
        raise ControlPlaneError("name prefix is invalid")
    backends = [f"{name_prefix}-{surface}" for surface in ("portal", "rm", "ops")]
    services = [
        *backends,
        *(f"{name_prefix}-{app_id}-{surface}" for app_id in _APP_IDS for surface in ("ui", "api")),
    ]
    if len(services) != _EXPECTED_SERVICE_COUNT:
        raise ControlPlaneError("internal service inventory is incomplete")
    component_by_service = {
        f"{name_prefix}-portal": "bff",
        f"{name_prefix}-rm": "rm",
        f"{name_prefix}-ops": "ops",
        **{
            f"{name_prefix}-{app_id}-{surface}": f"{app_id}-{surface}"
            for app_id in _APP_IDS
            for surface in ("ui", "api")
        },
    }
    expected_images = _expected_images(expected_images_json, set(component_by_service.values()))
    listed_backends = _listed_names(
        runner.fetch(
            [
                "compute",
                "backend-services",
                "list",
                "--global",
                f"--filter=name~^{name_prefix}-",
                f"--project={project_id}",
            ]
        ),
        "backend services",
    )
    if listed_backends != set(backends):
        raise ControlPlaneError(
            "backend resources with the reviewed prefix are incomplete or unexpected"
        )
    listed_services = _listed_names(
        runner.fetch(
            [
                "run",
                "services",
                "list",
                f"--region={region}",
                f"--filter=metadata.name~^{name_prefix}-",
                f"--project={project_id}",
            ]
        ),
        "Cloud Run services",
    )
    if listed_services != set(services):
        raise ControlPlaneError(
            "Cloud Run resources with the reviewed prefix are incomplete or unexpected"
        )
    for name in backends:
        resource = runner.fetch(
            [
                "compute",
                "backend-services",
                "describe",
                name,
                "--global",
                f"--project={project_id}",
            ]
        )
        if not isinstance(resource, dict):
            raise ControlPlaneError(f"backend {name} returned a malformed resource")
        if resource.get("name") != name:
            raise ControlPlaneError(f"backend {name} resolved to a different resource")
        iap = resource.get("iap")
        if (
            not isinstance(iap, dict)
            or iap.get("enabled") is not True
            or iap.get("oauth2ClientId") != iap_client_id
        ):
            raise ControlPlaneError(f"backend {name} does not use the reviewed IAP client")
        if (
            resource.get("loadBalancingScheme") != "EXTERNAL_MANAGED"
            or resource.get("protocol") != "HTTP"
            or not resource.get("backends")
        ):
            raise ControlPlaneError(f"backend {name} has an unexpected edge configuration")

    for name in services:
        resource = runner.fetch(
            [
                "run",
                "services",
                "describe",
                name,
                f"--region={region}",
                f"--project={project_id}",
            ]
        )
        if not isinstance(resource, dict):
            raise ControlPlaneError(f"Cloud Run service {name} returned a malformed resource")
        metadata = resource.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("name") != name:
            raise ControlPlaneError(f"Cloud Run service {name} resolved to a different resource")
        labels = metadata.get("labels", {})
        if labels.get("cloud.googleapis.com/location") != region:
            raise ControlPlaneError(f"Cloud Run service {name} is outside {region}")
        containers = (
            resource.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        )
        expected_image = expected_images[component_by_service[name]]
        if (
            not isinstance(containers, list)
            or len(containers) != 1
            or not isinstance(containers[0], dict)
            or containers[0].get("image") != expected_image
        ):
            raise ControlPlaneError(
                f"Cloud Run service {name} does not run its exact reviewed image"
            )
    print(
        f"PASS GCP control plane: {len(backends)} IAP backends and "
        f"{len(services)} regional digest-pinned services"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--iap-client-id", required=True)
    parser.add_argument("--name-prefix", required=True)
    parser.add_argument("--expected-images-json", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        verify(
            GcloudRunner(),
            project_id=args.project_id,
            region=args.region,
            iap_client_id=args.iap_client_id,
            name_prefix=args.name_prefix,
            expected_images_json=args.expected_images_json,
        )
    except ControlPlaneError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

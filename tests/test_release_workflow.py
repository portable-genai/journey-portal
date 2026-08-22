from pathlib import Path

WORKFLOW = Path(".github/workflows/release-images.yaml")


def test_release_tag_is_promoted_only_after_digest_scan_and_signature() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    quarantine = text.index(":quarantine-${{ github.run_id }}-${{ github.run_attempt }}")
    scan = text.index("aquasecurity/trivy-action")
    sign = text.index('cosign sign --yes "${IMAGE}@${DIGEST}"')
    promote = text.index('imagetools create --tag "${IMAGE}:${VERSION}"')

    assert quarantine < scan < sign < promote
    assert (
        "tags: ${{ inputs.registry_host }}/${{ inputs.repository }}/"
        "hrz9-${{ matrix.name }}:${{ inputs.version }}"
    ) not in text


def test_scan_sign_and_promotion_all_use_the_build_digest() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert text.count("DIGEST: ${{ steps.build.outputs.digest }}") >= 3
    assert "image-ref: ${{ inputs.registry_host }}/${{ inputs.repository }}/hrz9-" in text
    assert "@${{ steps.build.outputs.digest }}" in text

"""Platform SubjectTokenPort: the same pending Google ID-token source as the managed profile."""

from __future__ import annotations

from ..gcp.subject_token import PendingGoogleSubjectTokenAdapter


class PlatformSubjectTokenAdapter(PendingGoogleSubjectTokenAdapter):
    """The platform deployment awaits the same dedicated OAuth client and portal OIDC session."""

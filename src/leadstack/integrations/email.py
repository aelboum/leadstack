"""Email delivery adapter over SaaS-OS `core.email`.

LeadStack sends through SaaS-OS's provider-agnostic `send_email()` (SMTP by
default, configured by the platform's `EmailConfig` and secrets). This module
only selects the provider, so tests can install SaaS-OS's own
`FakeEmailProvider` without patching platform code.

There is no fake path in production: when no provider override is
installed, the real SaaS-OS default (SMTP) is used, and an unconfigured SMTP
setup fails loudly (`EmailConfigurationError`) rather than pretending to
send.
"""

from __future__ import annotations

from core.email import EmailMessage, EmailProvider, EmailSendResult, send_email

_override: EmailProvider | None = None


def install_provider(provider: EmailProvider | None) -> None:
    """Test hook: route sends to `provider` (e.g. `FakeEmailProvider`).
    `None` restores the SaaS-OS default."""
    global _override
    _override = provider


def deliver(message: EmailMessage) -> EmailSendResult:
    return send_email(message, provider=_override)

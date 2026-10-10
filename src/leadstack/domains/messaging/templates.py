"""Text of the emails LeadStack itself writes, per workspace language.

The language is the *workspace's* configured locale (the business chooses
how it addresses its contacts); it is never inferred from the recipient's
address, country or name. Unknown locales fall back to English.

Placeholders for links are substituted at delivery time (links are never
stored): see `service.CONFIRM_PLACEHOLDER` / `UNSUBSCRIBE_PLACEHOLDER`.

The French and Arabic texts were written without review by a native
speaker and must be reviewed before launch (docs/audits/phase-13-groundwork.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from leadstack.domains import regional

FALLBACK_LOCALE = "en"


@dataclass(frozen=True)
class ConsentTexts:
    confirm_subject: str  # "{workspace}" is replaced
    confirm_intro: str  # "{workspace}" is replaced
    confirm_action: str
    confirm_ignore: str
    unsubscribe_footer: str


_TEXTS: dict[str, ConsentTexts] = {
    "en": ConsentTexts(
        confirm_subject="Please confirm - {workspace}",
        confirm_intro="{workspace} asks you to confirm that you want to receive its emails.",
        confirm_action="Confirm:",
        confirm_ignore="If you did not request this, ignore this email or unsubscribe:",
        unsubscribe_footer="Unsubscribe:",
    ),
    "fr": ConsentTexts(
        confirm_subject="Merci de confirmer - {workspace}",
        confirm_intro=(
            "{workspace} vous demande de confirmer que vous souhaitez recevoir ses e-mails."
        ),
        confirm_action="Confirmer :",
        confirm_ignore=(
            "Si vous n’êtes pas à l’origine de cette demande, "
            "ignorez cet e-mail ou désabonnez-vous :"
        ),
        unsubscribe_footer="Se désabonner :",
    ),
    "ar": ConsentTexts(
        confirm_subject="يرجى التأكيد - {workspace}",
        confirm_intro="تطلب منك {workspace} تأكيد رغبتك في تلقي رسائلها الإلكترونية.",
        confirm_action="للتأكيد:",
        confirm_ignore="إذا لم تطلب ذلك، تجاهل هذه الرسالة أو ألغِ اشتراكك:",
        unsubscribe_footer="لإلغاء الاشتراك:",
    ),
}

if set(_TEXTS) != set(regional.SUPPORTED_LOCALES):  # pragma: no cover -- import-time guard
    raise RuntimeError("every supported locale needs email texts")


def consent_texts(locale: str | None) -> ConsentTexts:
    return _TEXTS.get(locale or "", _TEXTS[FALLBACK_LOCALE])

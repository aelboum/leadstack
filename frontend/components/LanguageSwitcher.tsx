"use client";

import { useRouter } from "next/navigation";
import { useI18n } from "@/components/I18nProvider";
import { LOCALE_COOKIE, LOCALES, type Locale } from "@/lib/i18n/locale";

const NAMES: Record<Locale, string> = { fr: "Français", ar: "العربية", en: "English" };

export function setLocaleCookie(locale: Locale): void {
  const secure = typeof location !== "undefined" && location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${LOCALE_COOKIE}=${locale}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
}

export function LanguageSwitcher() {
  const { locale, t } = useI18n();
  const router = useRouter();
  return (
    <label className="language">
      <span>{t.language.label}</span>
      <select
        value={locale}
        onChange={(event) => {
          setLocaleCookie(event.target.value as Locale);
          router.refresh();
        }}
      >
        {LOCALES.map((code) => (
          <option key={code} value={code} lang={code}>
            {NAMES[code]}
          </option>
        ))}
      </select>
    </label>
  );
}

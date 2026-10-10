"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { Dictionary } from "@/lib/i18n/dictionaries";
import { dictionary, direction, type Locale } from "@/lib/i18n/locale";

interface I18n {
  locale: Locale;
  dir: "ltr" | "rtl";
  t: Dictionary;
}

const I18nContext = createContext<I18n | null>(null);

export function I18nProvider({ locale, children }: { locale: Locale; children: ReactNode }) {
  return (
    <I18nContext.Provider value={{ locale, dir: direction(locale), t: dictionary(locale) }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n(): I18n {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside <I18nProvider>");
  return value;
}

/** Translated message for an API error code, with a generic fallback. */
export function useErrorMessage(): (code: string) => string {
  const { t } = useI18n();
  return (code) => (t.errors as Record<string, string>)[code] ?? t.errors.generic;
}

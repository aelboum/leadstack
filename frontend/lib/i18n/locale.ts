import { dictionaries, type Dictionary } from "./dictionaries";

export const LOCALES = ["fr", "ar", "en"] as const;
export type Locale = (typeof LOCALES)[number];
export const LOCALE_COOKIE = "leadstack_locale";

// Deployment-configurable fallback; not tied to any country.
export const DEFAULT_LOCALE: Locale = isLocale(process.env.NEXT_PUBLIC_DEFAULT_LOCALE)
  ? process.env.NEXT_PUBLIC_DEFAULT_LOCALE
  : "en";

const RTL: ReadonlySet<Locale> = new Set<Locale>(["ar"]);

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

export function direction(locale: Locale): "rtl" | "ltr" {
  return RTL.has(locale) ? "rtl" : "ltr";
}

export function dictionary(locale: Locale): Dictionary {
  return dictionaries[locale];
}

/**
 * Locale resolution: an explicit choice (cookie) wins; otherwise the
 * browser's Accept-Language preferences, in order; otherwise the default.
 * Never inferred from location or IP address.
 */
export function resolveLocale(cookieValue: string | undefined, acceptLanguage: string | null): Locale {
  if (isLocale(cookieValue)) return cookieValue;
  for (const part of (acceptLanguage ?? "").split(",")) {
    const tag = part.split(";")[0]?.trim().toLowerCase() ?? "";
    const base = tag.split("-")[0];
    if (isLocale(base)) return base;
  }
  return DEFAULT_LOCALE;
}

/** Latin digits in every locale (phone numbers, amounts stay unambiguous). */
export function intlLocale(locale: Locale): string {
  return locale === "ar" ? "ar-u-nu-latn" : locale;
}

export function formatDate(value: string | Date, locale: Locale, timeZone?: string): string {
  return new Intl.DateTimeFormat(intlLocale(locale), { dateStyle: "medium", timeZone }).format(
    typeof value === "string" ? new Date(value) : value,
  );
}

/**
 * Formats a decimal amount string (exact, never a float) in its currency.
 * Uses Intl's string input so no binary floating point is involved.
 */
export function formatMoney(amount: string, currency: string, locale: Locale): string {
  const format = new Intl.NumberFormat(intlLocale(locale), { style: "currency", currency });
  return format.format(amount as unknown as number);
}

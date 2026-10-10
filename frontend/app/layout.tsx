import type { Metadata } from "next";
import { cookies, headers } from "next/headers";
import Link from "next/link";
import type { ReactNode } from "react";
import { I18nProvider } from "@/components/I18nProvider";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { dictionary, direction, LOCALE_COOKIE, resolveLocale } from "@/lib/i18n/locale";
import "./globals.css";

export const metadata: Metadata = { title: "LeadStack" };

export default async function RootLayout({ children }: { children: ReactNode }) {
  const locale = resolveLocale(
    (await cookies()).get(LOCALE_COOKIE)?.value,
    (await headers()).get("accept-language"),
  );
  const t = dictionary(locale);
  return (
    // `dir` on <html> makes the whole layout mirror for Arabic; styles use
    // logical properties (inline-start/end), never left/right.
    <html lang={locale} dir={direction(locale)}>
      <body>
        <I18nProvider locale={locale}>
          <header className="topbar">
            <Link href="/" className="brand">
              {t.app.name}
            </Link>
            <LanguageSwitcher />
          </header>
          <main className="page">{children}</main>
        </I18nProvider>
      </body>
    </html>
  );
}

"use client";

import Link from "next/link";
import { useI18n } from "@/components/I18nProvider";
import { seg } from "@/lib/api";

export function WorkspaceTabs({ tenantId }: { tenantId: string }) {
  const { t } = useI18n();
  return (
    <nav className="tabs" aria-label={t.nav.workspaces}>
      <Link href="/">{t.nav.workspaces}</Link>
      <Link href={`/w/${seg(tenantId)}/contacts`}>{t.nav.contacts}</Link>
      <Link href={`/w/${seg(tenantId)}/companies`}>{t.nav.companies}</Link>
      <Link href={`/w/${seg(tenantId)}/deals`}>{t.nav.deals}</Link>
      <Link href={`/w/${seg(tenantId)}/forms`}>{t.nav.forms}</Link>
      <Link href={`/w/${seg(tenantId)}/automations`}>{t.nav.automations}</Link>
      <Link href={`/w/${seg(tenantId)}/calendar`}>{t.nav.calendar}</Link>
    </nav>
  );
}

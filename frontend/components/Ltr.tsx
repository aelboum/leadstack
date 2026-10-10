import type { ReactNode } from "react";

/**
 * Left-to-right isolate for values that must never be reordered by the
 * surrounding text direction: phone numbers, email addresses, codes,
 * amounts. In an Arabic (RTL) page, "+212 6 12 34 56 78" would otherwise be
 * displayed with its parts reversed.
 */
export function Ltr({ children }: { children: ReactNode }) {
  return <bdi dir="ltr">{children}</bdi>;
}

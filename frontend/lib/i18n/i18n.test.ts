import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { dictionaries } from "./dictionaries";
import { direction, formatDate, isLocale, LOCALES, resolveLocale } from "./locale";

function keys(value: unknown, prefix = ""): string[] {
  if (typeof value !== "object" || value === null) return [prefix];
  return Object.entries(value).flatMap(([k, v]) => keys(v, prefix ? `${prefix}.${k}` : k));
}

describe("dictionaries", () => {
  const reference = keys(dictionaries.en).sort();

  it.each(LOCALES)("%s has exactly the reference keys", (locale) => {
    expect(keys(dictionaries[locale]).sort()).toEqual(reference);
  });

  it.each(LOCALES)("%s has no empty strings", (locale) => {
    const values = keys(dictionaries[locale]).map((path) =>
      path.split(".").reduce<unknown>((node, part) => (node as Record<string, unknown>)[part], dictionaries[locale]),
    );
    expect(values.every((v) => typeof v === "string" && v.trim().length > 0)).toBe(true);
  });

  it("arabic strings are actually arabic", () => {
    expect(dictionaries.ar.contacts.title).toMatch(/[؀-ۿ]/);
  });
});

describe("locale", () => {
  it("only arabic is right-to-left", () => {
    expect(LOCALES.filter((l) => direction(l) === "rtl")).toEqual(["ar"]);
  });

  it("an explicit choice wins over browser preferences", () => {
    expect(resolveLocale("ar", "fr-FR,fr;q=0.9")).toBe("ar");
  });

  it("falls back through Accept-Language in order", () => {
    expect(resolveLocale(undefined, "de-DE,ar-MA;q=0.8,fr;q=0.5")).toBe("ar");
    expect(resolveLocale("xx", "fr-CA")).toBe("fr");
  });

  it("uses the default for unsupported preferences", () => {
    expect(isLocale(resolveLocale(undefined, "de,it"))).toBe(true);
    expect(isLocale(resolveLocale(undefined, null))).toBe(true);
  });

  it("formats dates in arabic with latin digits and in the given time zone", () => {
    const formatted = formatDate("2026-03-01T23:30:00Z", "ar", "Africa/Casablanca");
    expect(formatted).toMatch(/2026/);
    expect(formatted).not.toMatch(/[٠-٩]/);
  });
});

describe("structural RTL", () => {
  // Physical left/right breaks mirroring in Arabic; logical properties only.
  const PHYSICAL = /\b(margin|padding|border)-(left|right)\b|\b(left|right)\s*:|text-align:\s*(left|right)|float:\s*(left|right)/;

  function sources(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const path = join(dir, name);
      if (["node_modules", ".next"].includes(name)) return [];
      if (statSync(path).isDirectory()) return sources(path);
      return /\.(css|tsx)$/.test(name) ? [path] : [];
    });
  }

  it("no stylesheet or component uses physical left/right", () => {
    const offenders = sources(join(__dirname, "..", "..")).filter((file) =>
      PHYSICAL.test(readFileSync(file, "utf8")),
    );
    expect(offenders).toEqual([]);
  });
});

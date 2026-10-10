import { describe, expect, it } from "vitest";
import { safeRedirect } from "@/components/PublicForm";

describe("public form redirect", () => {
  it("follows only https URLs", () => {
    expect(safeRedirect("https://example.ma/merci")).toBe("https://example.ma/merci");
    expect(safeRedirect("http://example.ma")).toBeNull();
    expect(safeRedirect("javascript:alert(1)")).toBeNull();
    expect(safeRedirect("//evil.example")).toBeNull();
    expect(safeRedirect(null)).toBeNull();
  });
});

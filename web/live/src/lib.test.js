import { describe, expect, it, test } from "vitest";
import { extent, freshness, hhmm, pct, scale, ticks, zLevel } from "./lib.js";

describe("pct", () => {
  test("signs and digits", () => {
    expect(pct(0.0123)).toBe("+1.23%");
    expect(pct(-0.025, 1)).toBe("-2.5%");
    expect(pct(0)).toBe("0.00%");
    expect(pct(null)).toBe("--");
    expect(pct(NaN)).toBe("--");
  });
});

describe("zLevel", () => {
  test("thresholds", () => {
    expect(zLevel(-2.5, -2)).toBe("alert");
    expect(zLevel(-2, -2)).toBe("alert");
    expect(zLevel(-1.2, -2)).toBe("watch");
    expect(zLevel(-0.5, -2)).toBe("normal");
    expect(zLevel(3, -2)).toBe("normal");
    expect(zLevel(null, -2)).toBe("unknown");
  });
});

describe("freshness", () => {
  const now = Date.parse("2026-04-10T16:00:00Z");
  test("recent, overdue, none", () => {
    expect(freshness("2026-04-10T15:58:00Z", 120, now)).toBe("live");
    expect(freshness("2026-04-10T15:50:00Z", 120, now)).toBe("stale");
    expect(freshness(null, 120, now)).toBe("offline");
  });
});

describe("chart helpers", () => {
  test("scale maps the domain onto the range", () => {
    const y = scale([-0.02, 0.02], [200, 0]);
    expect(y(-0.02)).toBe(200);
    expect(y(0)).toBe(100);
    expect(y(0.02)).toBe(0);
  });
  test("ticks are round and inside the range", () => {
    expect(ticks(-0.031, 0.012, 4)).toEqual([-0.03, -0.02, -0.01, 0, 0.01]);
    expect(ticks(1, 1)).toEqual([]);
  });
  test("extent always includes zero", () => {
    const [lo, hi] = extent([0.01, 0.02]);
    expect(lo).toBeLessThan(0);
    expect(hi).toBeGreaterThan(0.02);
  });
  test("hhmm", () => {
    expect(hhmm("2026-04-10T15:55:00-04:00")).toBe("15:55");
    expect(hhmm(null)).toBe("--:--");
  });
});

import { labelIndexes, nearestIndex, pickRange } from "./lib.js";

describe("hover and ranges", () => {
  it("maps a pointer position to the nearest point", () => {
    expect(nearestIndex(0, 0, 100, 11)).toBe(0);
    expect(nearestIndex(54, 0, 100, 11)).toBe(5);
    expect(nearestIndex(500, 0, 100, 11)).toBe(10);
    expect(nearestIndex(-20, 0, 100, 11)).toBe(0);
    expect(nearestIndex(30, 0, 100, 1)).toBe(0);
  });
  it("spaces axis labels evenly and keeps both ends", () => {
    expect(labelIndexes(3, 7)).toEqual([0, 1, 2]);
    const l = labelIndexes(78, 7);
    expect(l[0]).toBe(0);
    expect(l[l.length - 1]).toBe(77);
    expect(l.length).toBe(7);
    expect(labelIndexes(0)).toEqual([]);
  });
  it("falls back to 1D when the saved range is missing", () => {
    expect(pickRange("1Y", ["1H", "1D", "1Y"])).toBe("1Y");
    expect(pickRange("1Y", ["1H", "3H", "1D", "5D"])).toBe("1D");
    expect(pickRange("1M", ["1H"])).toBe("1H");
    expect(pickRange("1D", [])).toBe(null);
  });
});

import { fundHref, num, parseRoute, stockHref, usd } from "./lib.js";

describe("pages", () => {
  it("formats dollar amounts", () => {
    expect(usd(1234567890)).toBe("$1.23B");
    expect(usd(45_600_000)).toBe("$45.6M");
    expect(usd(312_000_000_000)).toBe("$312B");
    expect(usd(950)).toBe("$950");
    expect(usd(-2_500_000)).toBe("-$2.50M");
    expect(usd(null)).toBe("--");
    expect(num(1234567.4)).toBe("1,234,567");
  });
  it("routes by hash and round-trips names with spaces", () => {
    expect(parseRoute("")).toEqual({ page: "live", key: null });
    expect(parseRoute("#/stock/NVDA")).toEqual({ page: "stock", key: "NVDA" });
    expect(parseRoute(fundHref("Two Sigma"))).toEqual({ page: "fund", key: "Two Sigma" });
    expect(parseRoute(stockHref("BRK.B"))).toEqual({ page: "stock", key: "BRK.B" });
    expect(parseRoute("#/other/x")).toEqual({ page: "live", key: null });
    expect(parseRoute("#/fund/%E0%A4%A")).toEqual({ page: "fund", key: "%E0%A4%A" });
  });
});

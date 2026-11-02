import { describe, expect, test } from "vitest";
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

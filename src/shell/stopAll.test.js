import { describe, expect, it, vi } from "vitest";
import { runStopAll } from "./stopAll.js";

const cars = [
  { id: "r1", name: "Rover" },
  { id: "r2", name: "Grip" },
];
const never = () => new Promise(() => {});

function recorder() {
  const results = {};
  const order = [];
  return { results, order, report: (key, result) => (results[key] = { ...results[key], ...result }) };
}

describe("runStopAll", () => {
  it("sends every car's stop before the Executive's cancel, in the same tick", () => {
    const rec = recorder();
    const stopCar = vi.fn((car) => (rec.order.push(`stop ${car.id}`), never()));
    const cancelMission = vi.fn(() => (rec.order.push("cancel"), never()));
    runStopAll({ cars, stopCar, cancelMission, missionActive: true, report: rec.report });
    expect(rec.order).toEqual(["stop r1", "stop r2", "cancel"]);
    expect(rec.results).toMatchObject({ r1: { state: "sending" }, r2: { state: "sending" }, mission: { state: "sending" } });
  });

  it("reports each car's stop as soon as it answers, while the Executive never does", async () => {
    const rec = recorder();
    const runs = runStopAll({
      cars,
      stopCar: (car) => Promise.resolve(car.id === "r1" ? { ok: true } : { ok: false, error: "no response" }),
      cancelMission: never,
      missionActive: true,
      report: rec.report,
    });
    await Promise.all(runs.cars);
    expect(rec.results.r1).toEqual({ name: "Rover", state: "sent" });
    expect(rec.results.r2).toEqual({ name: "Grip", state: "failed", error: "no response" });
    expect(rec.results.mission.state).toBe("sending");
  });

  it("a cancel that fails or throws at once does not touch the car stops", async () => {
    const rec = recorder();
    const runs = runStopAll({
      cars: cars.slice(0, 1),
      stopCar: () => Promise.resolve({ ok: true }),
      cancelMission: () => {
        throw new Error("Failed to fetch");
      },
      missionActive: true,
      report: rec.report,
    });
    await Promise.all([...runs.cars, runs.mission]);
    expect(rec.results.r1.state).toBe("sent");
    expect(rec.results.mission).toEqual({ state: "failed", error: "Failed to fetch" });
  });

  it("reports a cancel that went through, and a superseded or throwing car stop", async () => {
    const rec = recorder();
    const runs = runStopAll({
      cars,
      stopCar: (car) => {
        if (car.id === "r2") throw new Error("boom");
        return Promise.resolve({ ok: true, superseded: true });
      },
      cancelMission: () => Promise.resolve({ mission: { status: "canceling" } }),
      missionActive: true,
      report: rec.report,
    });
    await Promise.all([...runs.cars, runs.mission]);
    expect(rec.results.r1).toEqual({ name: "Rover", state: "superseded" });
    expect(rec.results.r2).toEqual({ name: "Grip", state: "failed", error: "boom" });
    expect(rec.results.mission).toEqual({ state: "sent" });
  });

  it("reports no mission when the Executive answers that nothing was running", async () => {
    const rec = recorder();
    const runs = runStopAll({ cars: [], stopCar: vi.fn(), cancelMission: () => Promise.resolve({ ok: true, none: true }), missionActive: true, report: rec.report });
    await runs.mission;
    expect(rec.results.mission).toEqual({ state: "none" });
  });

  it("does not call the Executive without an active mission", () => {
    const rec = recorder();
    const cancelMission = vi.fn();
    const runs = runStopAll({ cars: [], stopCar: vi.fn(), cancelMission, missionActive: false, report: rec.report });
    expect(cancelMission).not.toHaveBeenCalled();
    expect(runs.mission).toBeNull();
    expect(rec.results.mission).toEqual({ state: "none" });
  });
});

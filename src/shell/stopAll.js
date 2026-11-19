// STOP ALL: a software stop for the whole fleet. Every connected car gets live-data's own stop, and
// an active mission gets the Executive's cancel. The car stops go out first, in the same tick as the
// cancel, and never wait on the Executive; each outcome is reported on its own as it lands.

const message = (error) => error?.message || String(error);

/* Calls fn now; a throw or a rejection becomes { ok: false, error }. */
function settle(fn) {
  try {
    return Promise.resolve(fn()).then(
      (value) => value ?? { ok: true },
      (error) => ({ ok: false, error: message(error) }),
    );
  } catch (error) {
    return Promise.resolve({ ok: false, error: message(error) });
  }
}

/* cars: the connected live-data robots. stopCar(car) resolves to { ok, error?, superseded? } (App's
   stopDrive). cancelMission() resolves with the Executive's state ({ none: true } when nothing
   was running) or rejects. report(key, result):
   key is a car id or "mission". Returns the promises, for tests and callers that want to wait. */
export function runStopAll({ cars, stopCar, cancelMission, missionActive, report }) {
  const carRuns = cars.map((car) => {
    report(car.id, { name: car.name, state: "sending" });
    return settle(() => stopCar(car)).then((result) => {
      const state = !result.ok ? "failed" : result.superseded ? "superseded" : "sent";
      report(car.id, result.ok ? { name: car.name, state, error: undefined } : { name: car.name, state, error: result.error });
      return result;
    });
  });
  if (!missionActive) {
    report("mission", { state: "none" });
    return { cars: carRuns, mission: null };
  }
  report("mission", { state: "sending" });
  const missionRun = settle(cancelMission).then((result) => {
    const ok = result.ok !== false;
    const state = !ok ? "failed" : result.none ? "none" : "sent";
    report("mission", ok ? { state, error: undefined } : { state, error: result.error });
    return result;
  });
  return { cars: carRuns, mission: missionRun };
}

// Probe the MJPEG URL before handing it to an <img>: the backend answers 409 camera_untuned with a JSON
// reason, which an <img> cannot read. Resolves {state: "ready" | "blocked" | "error", reason?}.
// On 200 the probe is aborted (via `abort`) so the <img> opens the one real stream.
export async function probeCamera(fetchFn, url, { signal, abort } = {}) {
  let response;
  try {
    response = await fetchFn(url, { signal });
  } catch (error) {
    if (error?.name === "AbortError") return { state: "aborted" };
    return { state: "error", reason: "Could not reach the camera stream." };
  }
  if (response.ok) {
    abort?.();
    return { state: "ready" };
  }
  const body = await response.json().catch(() => ({}));
  if (response.status === 409) {
    return { state: "blocked", reason: body.reason || body.error || "The camera is not tuned for streaming yet." };
  }
  return { state: "error", reason: body.error || `Camera request failed (${response.status}).` };
}

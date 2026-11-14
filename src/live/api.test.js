import { describe, expect, it, vi } from "vitest";
import { cameraUrl, postCamera, streamUrl } from "./api.js";

const base = "http://127.0.0.1:8787/api";
const robot = { host: "10.0.0.5", domainId: 42 };

describe("streamUrl", () => {
  it("comma-joins and encodes topics", () => {
    const url = streamUrl(base, robot, ["/a/b", "/c d"]);
    expect(url).toBe(`${base}/robots/stream?ip=10.0.0.5&domain_id=42&topics=${encodeURIComponent("/a/b,/c d")}`);
  });
  it("handles no topics", () => {
    expect(streamUrl(base, robot, [])).toBe(`${base}/robots/stream?ip=10.0.0.5&domain_id=42&topics=`);
  });
});

describe("cameraUrl", () => {
  it("builds the mjpg url", () => {
    expect(cameraUrl(base, robot, "/cam/image_raw")).toBe(
      `${base}/robots/camera.mjpg?ip=10.0.0.5&domain_id=42&topic=${encodeURIComponent("/cam/image_raw")}`,
    );
  });
  it("adds force_full", () => {
    expect(cameraUrl(base, robot, "/c", { forceFull: true })).toMatch(/&force_full=1$/);
  });
});

describe("postCamera", () => {
  it("posts and unwraps the camera dict", async () => {
    const postApi = vi.fn().mockResolvedValue({ ok: true, camera: { applied: true } });
    const out = await postCamera(postApi, robot, { quality: "high", depth: true });
    expect(postApi).toHaveBeenCalledWith("/robots/camera", { ip: "10.0.0.5", domain_id: 42, quality: "high", depth: true });
    expect(out).toEqual({ applied: true });
  });
  it("returns a bare camera dict as is", async () => {
    const postApi = vi.fn().mockResolvedValue({ applied: false });
    expect(await postCamera(postApi, robot, { quality: "low", depth: false })).toEqual({ applied: false });
  });
});

describe("streamUrl images", () => {
  it("adds the images list only when there is one", () => {
    expect(streamUrl(base, robot, ["/a"], ["/cam"])).toBe(`${base}/robots/stream?ip=10.0.0.5&domain_id=42&topics=${encodeURIComponent("/a")}&images=${encodeURIComponent("/cam")}`);
  });
});

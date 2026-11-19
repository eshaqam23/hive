import { describe, expect, it } from "vitest";
import { lineSplitter } from "./prefix.mjs";

function collect(prefix) {
  const out = [];
  const s = lineSplitter(prefix, (line) => out.push(line));
  return { s, out };
}

describe("lineSplitter", () => {
  it("prefixes every complete line", () => {
    const { s, out } = collect("[exec]");
    s.push("one\ntwo\n");
    expect(out).toEqual(["[exec] one\n", "[exec] two\n"]);
  });
  it("joins a line split across chunks", () => {
    const { s, out } = collect("[fleet]");
    s.push("hel");
    s.push("lo\nwor");
    expect(out).toEqual(["[fleet] hello\n"]);
    s.push("ld\n");
    expect(out).toEqual(["[fleet] hello\n", "[fleet] world\n"]);
  });
  it("drops the carriage return of CRLF lines", () => {
    const { s, out } = collect("[vite]");
    s.push("a\r\nb\r\n");
    expect(out).toEqual(["[vite] a\n", "[vite] b\n"]);
  });
  it("flushes a last line without a newline at the end", () => {
    const { s, out } = collect("[bridge]");
    s.push("tail");
    s.end();
    s.end();
    expect(out).toEqual(["[bridge] tail\n"]);
  });
  it("keeps blank lines", () => {
    const { s, out } = collect("[backend]");
    s.push("\n");
    expect(out).toEqual(["[backend] \n"]);
  });
});

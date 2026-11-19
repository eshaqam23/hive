// Prefix each line of a child's output with its name ([backend], [exec], ...).

// write(line) gets each complete line, prefixed and ending in "\n". end() flushes a last partial line.
export function lineSplitter(prefix, write) {
  let buf = "";
  return {
    push(chunk) {
      buf += chunk;
      let i;
      while ((i = buf.indexOf("\n")) !== -1) {
        write(`${prefix} ${buf.slice(0, i).replace(/\r$/, "")}\n`);
        buf = buf.slice(i + 1);
      }
    },
    end() {
      if (buf) write(`${prefix} ${buf.replace(/\r$/, "")}\n`);
      buf = "";
    },
  };
}

// Copies a readable stream (a child's stdout or stderr) to out (process.stdout / process.stderr).
export function pipePrefixed(stream, prefix, out) {
  if (!stream) return;
  const s = lineSplitter(prefix, (line) => out.write(line));
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => s.push(chunk));
  stream.on("end", () => s.end());
}

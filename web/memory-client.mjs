import { spawn } from "node:child_process";

export class MemoryCommandError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

export class MemoryClient {
  constructor(script) {
    this.script = script;
  }

  async run(command, args = [], input) {
    const executables = process.platform === "win32" ? ["python"] : ["python3", "python"];
    for (const executable of executables) {
      try {
        return await this.execute(executable, command, args, input);
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
    }
    throw new MemoryCommandError("unavailable", "找不到 Python 运行环境。");
  }

  execute(executable, command, args, input) {
    return new Promise((resolve, reject) => {
      const child = spawn(executable, ["-B", "-X", "utf8", this.script, command, ...args], {
        env: process.env,
        windowsHide: true,
        stdio: ["pipe", "pipe", "pipe"],
      });
      const stdout = [], stderr = [];
      let size = 0;
      let failure;
      const timer = setTimeout(() => {
        failure = new MemoryCommandError("timeout", "规则操作超时，请重新读取状态后重试。");
        child.kill();
      }, 30_000);
      for (const [stream, chunks] of [[child.stdout, stdout], [child.stderr, stderr]]) {
        stream.on("data", (chunk) => {
          size += chunk.length;
          if (size > 32 * 1024 * 1024) {
            failure = new MemoryCommandError("output_limit", "规则输出超过大小限制。");
            child.kill();
          } else {
            chunks.push(chunk);
          }
        });
      }
      child.on("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.stdin.on("error", (error) => {
        if (error.code !== "EPIPE") failure = error;
      });
      child.on("close", (code) => {
        clearTimeout(timer);
        if (failure) return reject(failure);
        try {
          const payload = JSON.parse(Buffer.concat(code === 0 ? stdout : stderr).toString("utf8"));
          if (code !== 0) return reject(new MemoryCommandError(payload.error.code, payload.error.message));
          resolve(payload);
        } catch {
          reject(new MemoryCommandError("invalid_response", "规则服务返回了无效响应。"));
        }
      });
      child.stdin.end(input === undefined ? undefined : JSON.stringify(input));
    });
  }
}

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

/**
 * wechat-reader extension
 *
 * Registers a `wechat_read` tool that fetches and parses WeChat Official
 * Account articles (mp.weixin.qq.com) via a curl_cffi-backed Python script.
 * The Python env is lazily provisioned on first use (uv venv + pip install).
 */

const EXT_DIR = (typeof __dirname !== "undefined"
  ? __dirname
  : new URL(".", import.meta.url).pathname);
const VENV_PY = join(homedir(), ".cache", "wechat-reader", "venv", "bin", "python");
const SCRIPT = join(EXT_DIR, "wechat_reader.py");
const PY_DEPS = ["curl_cffi", "beautifulsoup4"];

function uvBin(): string {
  const candidates = [
    join(homedir(), ".cargo", "bin", "uv"),
    join(homedir(), ".local", "bin", "uv"),
    "/usr/local/bin/uv",
    "/home/linuxbrew/.linuxbrew/bin/uv",
    "uv",
  ];
  for (const c of candidates) {
    if (c === "uv" || existsSync(c)) return c; // last resort: PATH lookup
  }
  return "uv";
}

let envPromise: Promise<string> | null = null;

/** Resolve to a python executable that has curl_cffi + bs4 installed. */
function ensurePython(pi: ExtensionAPI): Promise<string> {
  if (envPromise) return envPromise;
  envPromise = (async () => {
    // 1. Prefer dedicated venv
    if (existsSync(VENV_PY)) return VENV_PY;

    // 2. Reuse system python3 if deps are already importable.
    //    NOTE: pi.exec never rejects on non-zero exit — check code explicitly.
    const sysCheck = await pi.exec(
      "python3",
      ["-c", "import curl_cffi, bs4"],
      { timeout: 10000 },
    );
    if (sysCheck.code === 0) return "python3";

    // 3. Provision the venv (concurrent callers await the same promise)
    const uv = uvBin();
    const venvDir = join(homedir(), ".cache", "wechat-reader", "venv");
    const venv = await pi.exec(uv, ["venv", "--python", "3.12", venvDir], { timeout: 180000 });
    if (venv.code !== 0) {
      throw new Error(`uv venv failed (code ${venv.code}): ${venv.stderr.slice(-400)}`);
    }
    const install = await pi.exec(
      uv,
      ["pip", "install", "--python", VENV_PY, ...PY_DEPS],
      { timeout: 300000 },
    );
    if (install.code !== 0) {
      throw new Error(`uv pip install failed (code ${install.code}): ${install.stderr.slice(-400)}`);
    }
    if (!existsSync(VENV_PY)) {
      throw new Error("venv provisioned but python binary not found");
    }
    return VENV_PY;
  })();
  return envPromise;
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "wechat_read",
    label: "Read WeChat Article",
    description:
      "读取微信公众号文章（mp.weixin.qq.com 链接）并返回结构化内容：标题、作者、发布时间、正文与图片列表。使用 curl_cffi TLS 指纹伪装绕过微信反爬，无需登录或浏览器。微信外的链接请勿使用本工具。",
    promptSnippet: "fetch and parse mp.weixin.qq.com articles",
    promptGuidelines: [
      "Use wechat_read when the user provides a mp.weixin.qq.com article link and wants its content read, summarized, or archived.",
      "Do not use wechat_read for non-WeChat URLs; plain requests hit WeChat's anti-bot wall.",
    ],
    parameters: Type.Object({
      url: Type.String({ description: "微信公众号文章链接（https://mp.weixin.qq.com/s/...）" }),
      markdown: Type.Optional(Type.Boolean({ description: "正文是否以 Markdown 格式返回（保留代码块/引用/图片），默认 false 返回纯文本" })),
    }),
    async execute(_toolCallId, params, signal, onUpdate) {
      onUpdate?.({ content: [{ type: "text", text: "正在读取微信文章…" }] });
      try {
        const python = await ensurePython(pi);
        const args = [SCRIPT, params.url];
        if (params.markdown) args.push("--markdown");
        const { stdout } = await pi.exec(python, args, { signal, timeout: 90000 });

        const parsed = JSON.parse(stdout);
        if (!parsed.ok) {
          return {
            isError: true,
            content: [{ type: "text", text: `读取失败：${parsed.message || parsed.error}` }],
            details: { error: parsed.error, url: params.url },
          };
        }

        const header = [
          `公众号：${parsed.author ?? "未知"}`,
          `发布时间：${parsed.pub_time ?? "未知"}`,
          `图片数量：${parsed.images?.length ?? 0}`,
        ].join("\n");

        return {
          content: [
            {
              type: "text",
              text: [
                `标题：${parsed.title}`,
                header,
                "────────────────────",
                parsed.content,
                parsed.images?.length
                  ? `\n图片列表：\n${parsed.images.map((u: string) => `- ${u}`).join("\n")}`
                  : "",
              ].join("\n"),
            },
          ],
          details: {
            title: parsed.title,
            author: parsed.author,
            pub_time: parsed.pub_time,
            images: parsed.images ?? [],
            source_url: params.url,
          },
        };
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        return {
          isError: true,
          content: [{ type: "text", text: `读取失败：${msg}` }],
          details: { error: String(err), url: params.url },
        };
      }
    },
  });
}
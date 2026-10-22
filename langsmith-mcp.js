#!/usr/bin/env node
/**
 * LangSmith MCP stdio JSON-RPC Bridge
 * Bridges local IDE / Antigravity stdio MCP transport with LangSmith remote MCP endpoint.
 */

const https = require("https");
const readline = require("readline");

const API_KEY = process.env.LANGSMITH_API_KEY || "";
const HOST = process.env.LANGSMITH_ENDPOINT || "api.smith.langchain.com";

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  terminal: false,
});

rl.on("line", (line) => {
  if (!line || !line.trim()) return;

  try {
    const request = JSON.parse(line);
    handleRpc(request);
  } catch (err) {
    sendError(null, -32700, "Parse error: " + err.message);
  }
});

function handleRpc(req) {
  const { id, method, params } = req;

  if (method === "initialize") {
    sendResult(id, {
      protocolVersion: "2024-11-05",
      capabilities: {
        tools: { listChanged: false },
        resources: { subscribe: false, listChanged: false },
        prompts: { listChanged: false },
      },
      serverInfo: {
        name: "langsmith-mcp-bridge",
        version: "1.0.0",
      },
    });
    return;
  }

  if (method === "notifications/initialized") {
    return;
  }

  if (method === "ping") {
    sendResult(id, {});
    return;
  }

  // Forward RPC request to LangSmith Remote MCP
  const postData = JSON.stringify(req);
  const options = {
    hostname: HOST,
    port: 443,
    path: "/mcp",
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Content-Length": Buffer.byteLength(postData),
      "x-api-key": API_KEY,
    },
  };

  const httpRequest = https.request(options, (res) => {
    let data = "";
    res.on("data", (chunk) => {
      data += chunk;
    });
    res.on("end", () => {
      try {
        const responseJson = JSON.parse(data);
        sendRaw(responseJson);
      } catch (e) {
        sendError(id, -32603, "Internal proxy error: " + data.slice(0, 200));
      }
    });
  });

  httpRequest.on("error", (err) => {
    sendError(id, -32000, "LangSmith remote connection failed: " + err.message);
  });

  httpRequest.write(postData);
  httpRequest.end();
}

function sendResult(id, result) {
  sendRaw({ jsonrpc: "2.0", id, result });
}

function sendError(id, code, message) {
  sendRaw({ jsonrpc: "2.0", id, error: { code, message } });
}

function sendRaw(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

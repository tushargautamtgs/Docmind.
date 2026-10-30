const API_BASE = (
  import.meta.env.VITE_API_BASE_URL || "/api"
)
  .trim()
  .replace(/\/+$/, "");

const PUBLIC_UPLOAD =
  import.meta.env.VITE_PUBLIC_UPLOAD === "true";

const TOKEN_KEY = `docmind-workspace:${API_BASE}`;

let workspaceToken = "";
let sessionPromise = null;

function forgetWorkspace() {
  workspaceToken = "";

  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Browser storage may be unavailable.
  }
}

async function getWorkspace() {
  if (!workspaceToken) {
    try {
      workspaceToken =
        sessionStorage.getItem(TOKEN_KEY) || "";
    } catch {
      // Keep the token in memory instead.
    }
  }

  if (workspaceToken) return workspaceToken;

  if (!sessionPromise) {
    sessionPromise = (async () => {
      const response = await fetch(
        `${API_BASE}/session`,
        {
          method: "POST",
          headers: {
            "ngrok-skip-browser-warning": "1",
          },
        }
      );

      const data = await response
        .json()
        .catch(() => ({}));

      if (
        !response.ok ||
        typeof data.token !== "string" ||
        !data.token
      ) {
        throw new Error(
          "Could not start a browser workspace. " +
          "Check that app.public_upload:app is running."
        );
      }

      workspaceToken = data.token;

      try {
        sessionStorage.setItem(
          TOKEN_KEY,
          workspaceToken
        );
      } catch {
        // Keep the token in memory instead.
      }

      return workspaceToken;
    })().finally(() => {
      sessionPromise = null;
    });
  }

  return sessionPromise;
}

async function fetchApi(path, options = {}) {
  options.signal?.throwIfAborted();

  const headers = new Headers(options.headers);

  headers.set("ngrok-skip-browser-warning", "1");

  if (PUBLIC_UPLOAD) {
    const token = await getWorkspace();

    options.signal?.throwIfAborted();

    headers.set(
      "Authorization",
      `Bearer ${token}`
    );
  }

  const response = await fetch(
    `${API_BASE}${path}`,
    {
      ...options,
      headers,
    }
  );

  if (PUBLIC_UPLOAD && response.status === 401) {
    forgetWorkspace();
  }

  return response;
}

function responseError(response, data) {
  let message =
    typeof data?.detail === "string"
      ? data.detail
      : "";

  if (!message && Array.isArray(data?.detail)) {
    message = data.detail
      .map((item) => item.msg)
      .filter(Boolean)
      .join("; ");
  }

  if (!message) {
    message =
      response.status >= 500
        ? "The service could not complete this request. Check FastAPI and Ollama, then retry."
        : `Request failed (${response.status}). Please retry.`;
  }

  return new Error(message);
}

export function canonicalId(value) {
  const text = String(value || "").toLowerCase();
  const compact = text.replaceAll("-", "");

  if (!/^[a-f0-9]{32}$/.test(compact)) {
    return text;
  }

  return (
    `${compact.slice(0, 8)}-` +
    `${compact.slice(8, 12)}-` +
    `${compact.slice(12, 16)}-` +
    `${compact.slice(16, 20)}-` +
    compact.slice(20)
  );
}

export function documentName(document) {
  return (
    document?.filename ||
    document?.stored_filename ||
    "Untitled PDF"
  );
}

export function mergeDocuments(incoming, existing = []) {
  const items = new Map();

  for (const document of [...incoming, ...existing]) {
    const id = canonicalId(document.document_id);

    if (!id) continue;

    const previous = items.get(id);

    items.set(id, {
      ...document,
      ...previous,
      document_id: id,
      status:
        previous?.status === "indexed" ||
        document.status === "indexed"
          ? "indexed"
          : previous?.status || document.status,
    });
  }

  return [...items.values()];
}

export function patchMessage(messages, id, patch) {
  return messages.map((message) =>
    message.id === id
      ? { ...message, ...patch }
      : message
  );
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) {
    return "PDF document";
  }

  return bytes >= 1048576
    ? `${(bytes / 1048576).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function formatDuration(milliseconds) {
  const seconds = milliseconds / 1000;

  return seconds < 60
    ? `${seconds.toFixed(1)}s`
    : `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
}

export async function api(path, options = {}) {
  const response = await fetchApi(path, options);
  const text = await response.text();

  let data;

  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }

  if (!response.ok) {
    throw responseError(response, data);
  }

  if (data === null) {
    throw new Error(
      "The server returned an unreadable response. Check the API URL and ngrok connection."
    );
  }

  return data;
}

export async function readAnswerStream(
  documentId,
  question,
  signal,
  onUpdate
) {
  const response = await fetchApi(
    `/documents/${encodeURIComponent(documentId)}/ask/stream`,
    {
      method: "POST",
      signal,
      headers: {
        "Content-Type": "application/json",
        Accept: "application/x-ndjson",
      },
      body: JSON.stringify({
        question,
        top_k: 3,
      }),
    }
  );

  if (!response.ok) {
    const data = await response
      .json()
      .catch(() => ({}));

    throw responseError(response, data);
  }

  if (
    !response.body ||
    !response.headers
      .get("content-type")
      ?.includes("application/x-ndjson")
  ) {
    throw new Error(
      "Streaming response unavailable. Check the API URL and backend endpoint."
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  let buffer = "";
  let answer = "";
  let sources = [];
  let finished = false;

  function consume(line) {
    if (!line.trim()) return;

    let event;

    try {
      event = JSON.parse(line);
    } catch {
      throw new Error(
        "The stream contained invalid data. Please retry."
      );
    }

    if (
      !event ||
      typeof event !== "object" ||
      Array.isArray(event)
    ) {
      throw new Error("Invalid stream event.");
    }

    if (event.type === "error") {
      throw new Error(
        event.message || "Answer stream failed."
      );
    }

    if (event.type === "done") {
      finished = true;
      return;
    }

    if (
      event.type === "sources" &&
      Array.isArray(event.retrieved_sources)
    ) {
      sources = event.retrieved_sources;
    } else if (
      event.type === "delta" &&
      typeof event.text === "string"
    ) {
      answer += event.text;
    } else {
      throw new Error("Invalid stream event.");
    }

    onUpdate({ answer, sources });
  }

  try {
    while (!finished) {
      const { value, done } = await reader.read();

      buffer += done
        ? decoder.decode()
        : decoder.decode(value, { stream: true });

      let newline;

      while (
        !finished &&
        (newline = buffer.indexOf("\n")) !== -1
      ) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);

        consume(line);
      }

      if (done) {
        if (!finished && buffer.trim()) {
          consume(buffer);
        }

        break;
      }
    }

    if (!finished || !answer.trim()) {
      throw new Error(
        "The answer stream ended early. Please retry."
      );
    }

    return { answer, sources };
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
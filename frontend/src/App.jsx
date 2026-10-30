import { useEffect, useLayoutEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, canonicalId, documentName, formatBytes, formatDuration, mergeDocuments, patchMessage } from "./ui-utils";
import "./App.css";
import "./theme.css";
import { readAnswerStream } from "./ui-utils";

function Icon({ name, size = 19, ...props }) {
  const paths = {
    plus: <path d="M12 5v14M5 12h14" />,
    arrow: <path d="M12 19V5m-6 6 6-6 6 6" />,
    down: <path d="M12 5v14m-6-6 6 6 6-6" />,
    file: <><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><path d="M14 3v6h6M8 13h8M8 17h5" /></>,
    chat: <path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5z" />,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></>,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    copy: <><rect x="8" y="8" width="12" height="13" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2" /></>,
    sparkle: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z" /><path d="m20 2 .5 1.5L22 4l-1.5.5L20 6l-.5-1.5L18 4l1.5-.5z" /></>,
    book: <><path d="M12 5v16M3 4c4-1 6 0 9 2 3-2 5-3 9-2v15c-4-1-6 0-9 2-3-2-5-3-9-2z" /></>,
    shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z" /><path d="m8 12 3 3 5-6" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name] || paths.file}</svg>;
}

function Spinner() { return <span className="dm-spinner" aria-hidden="true" />; }

function PendingAnswer({ startedAt, onStop }) {
  const [seconds, setSeconds] = useState(() => Math.floor((performance.now() - startedAt) / 1000));
  useEffect(() => {
    const timer = setInterval(() => setSeconds(Math.floor((performance.now() - startedAt) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [startedAt]);
  return <div className="pending-answer">
    <div className="pending-main" role="status"><span className="dm-dots"><i /><i /><i /></span><span>Generating an answer</span><time>{seconds}s</time></div>
    <p>{seconds >= 20 ? "DocMind is still processing your question. You can stop at any time." : "Your question is being processed."}</p>
    <button className="text-button" onClick={onStop}>Stop waiting</button>
  </div>;
}

function CopyAnswer({ text }) {
  const [state, setState] = useState("");
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    try { await navigator.clipboard.writeText(text); setState("Copied"); }
    catch { setState("Copy unavailable — select the answer text"); }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState(""), 2500);
  }
  return <span className="copy-action"><button className="text-button" onClick={copy} aria-label="Copy answer"><Icon name="copy" size={14} /> Copy</button><span role="status">{state}</span></span>;
}

function Sources({ sources }) {
  if (!sources.length) return null;
  return <details className="dm-sources">
    <summary><Icon name="book" size={15} /><span>Retrieved passages</span><span className="tiny-count">{sources.length}</span><span className="disclosure">⌄</span></summary>
    <p className="source-notice">Context supplied to the model, not independently verified citations.</p>
    <div className="source-list">{sources.map((source, index) => <div className="source-item" key={`${source.document_id}-${source.chunk_id}-${index}`}>
      <div><span>PAGE {source.page}</span><span>Chunk {source.chunk_id}</span></div><p>{source.text}</p>
    </div>)}</div>
  </details>;
}

export default function App() {
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState("");
  const [indexingId, setIndexingId] = useState(null);
  const [selectedId, setSelectedId] = useState("");
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [chat, setChat] = useState([]);
  const [filter, setFilter] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showLatest, setShowLatest] = useState(false);
  const [loadVersion, setLoadVersion] = useState(0);
  const mounted = useRef(false);
  const controllers = useRef(new Set());
  const activeAsk = useRef(null);
  const indexLock = useRef(false);
  const uploadLock = useRef(false);
  const scroller = useRef(null);
  const nearBottom = useRef(true);
  const textarea = useRef(null);
  const uploadInput = useRef(null);
  const menuButton = useRef(null);
  const sidebar = useRef(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controllers.current.forEach(controller => controller.abort());
      controllers.current.clear();
      activeAsk.current = null;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    controllers.current.add(controller);
    setLoading(true); setLoadError("");
    api("/documents", { signal: controller.signal }).then(data => {
      if (controller.signal.aborted) return;
      if (!Array.isArray(data.documents)) throw new Error("The server returned an invalid document list.");
      setDocuments(previous => mergeDocuments(data.documents, previous));
    }).catch(err => {
      if (!controller.signal.aborted) setLoadError(err.message);
    }).finally(() => {
      controllers.current.delete(controller);
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [loadVersion]);

  useEffect(() => {
    if (!sidebarOpen || !window.matchMedia("(max-width: 760px)").matches) return;
    const previousFocus = document.activeElement;
    sidebar.current?.querySelector("button:not(:disabled), input:not(:disabled)")?.focus();
    function onKey(event) {
      if (event.key === "Escape") { setSidebarOpen(false); return; }
      if (event.key !== "Tab") return;
      const nodes = [...(sidebar.current?.querySelectorAll("button:not(:disabled), input:not(:disabled)") || [])].filter(node => node.getClientRects().length);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (!first) return;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); previousFocus?.focus({ preventScroll: true }); };
  }, [sidebarOpen]);

  useLayoutEffect(() => {
    if (!scroller.current) return;
    if (!chat.length) {
      scroller.current.scrollTop = 0;
      return;
    }
    if (nearBottom.current) scroller.current.scrollTop = scroller.current.scrollHeight;
    else setShowLatest(true);
  }, [chat]);

  useLayoutEffect(() => {
    const input = textarea.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
  }, [question]);

  function jumpToLatest() {
    nearBottom.current = true; setShowLatest(false);
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }

  function selectDocument(document) {
    if (activeAsk.current || document.status !== "indexed") return;
    const id = canonicalId(document.document_id);
    if (id !== selectedId) { setSelectedId(id); setChat([]); setQuestion(""); setError(""); nearBottom.current = true; setShowLatest(false); }
    setSidebarOpen(false);
  }
  async function handleUpload(event) {
  const input = event.target;
  const file = input.files?.[0];

  if (!file) return;

  if (
    uploadLock.current ||
    indexLock.current ||
    activeAsk.current
  ) {
    input.value = "";
    return;
  }

  setError("");
  setUploadMessage("");

  if (
    !file.name.toLowerCase().endsWith(".pdf") ||
    file.size > 10 * 1024 * 1024
  ) {
    setError("Choose a PDF no larger than 10 MB.");
    input.value = "";
    return;
  }

  const controller = new AbortController();
  controllers.current.add(controller);

  uploadLock.current = true;
  indexLock.current = true;
  setUploading(true);

  let uploaded = null;

  try {
    const form = new FormData();
    form.append("file", file);

    uploaded = await api("/documents/upload", {
      method: "POST",
      body: form,
      signal: controller.signal,
    });

    if (!uploaded.document_id) {
      throw new Error(
        "Upload response is missing its document ID."
      );
    }

    if (
      !mounted.current ||
      controller.signal.aborted
    ) return;

    const id = canonicalId(uploaded.document_id);

    setDocuments(previous =>
      mergeDocuments([uploaded], previous)
    );

    setUploading(false);
    setIndexingId(id);

    setUploadMessage(
      `${documentName(uploaded)} uploaded. Preparing for chat…`
    );

    await api(
      `/documents/${encodeURIComponent(id)}/index`,
      {
        method: "POST",
        signal: controller.signal,
      }
    );

    if (
      !mounted.current ||
      controller.signal.aborted
    ) return;

    setDocuments(previous =>
      previous.map(document =>
        document.document_id === id
          ? { ...document, status: "indexed" }
          : document
      )
    );

    setSelectedId(id);
    setChat([]);
    setQuestion("");
    setSidebarOpen(false);

    nearBottom.current = true;
    setShowLatest(false);

    setUploadMessage(
      `${documentName(uploaded)} is ready. Ask your question.`
    );

    requestAnimationFrame(() =>
      textarea.current?.focus()
    );
  } catch (error) {
    if (
      mounted.current &&
      error.name !== "AbortError"
    ) {
      setError(
        uploaded?.document_id
          ? `PDF saved, but preparation failed: ${error.message} Retry with Prepare for chat.`
          : error.message
      );
    }
  } finally {
    controllers.current.delete(controller);

    uploadLock.current = false;
    indexLock.current = false;
    input.value = "";

    if (mounted.current) {
      setUploading(false);
      setIndexingId(null);
    }
  }
}

  // async function handleUpload(event) {
  //   const input = event.target, file = input.files?.[0];
  //   if (!file || uploadLock.current) return;
  //   setError(""); setUploadMessage("");
  //   if (!file.name.toLowerCase().endsWith(".pdf") || file.size > 10 * 1024 * 1024) {
  //     setError("Choose a PDF no larger than 10 MB."); input.value = ""; return;
  //   }
  //   const controller = new AbortController(); controllers.current.add(controller);
  //   uploadLock.current = true; setUploading(true);
  //   try {
  //     const form = new FormData(); form.append("file", file);
  //     const uploaded = await api("/documents/upload", { method: "POST", body: form, signal: controller.signal });
  //     if (!uploaded.document_id) throw new Error("Upload response is missing its document ID.");
  //     if (mounted.current) {
  //       setDocuments(previous => mergeDocuments([uploaded], previous));
  //       setUploadMessage(`${documentName(uploaded)} uploaded. Prepare it to start chatting.`);
  //     }
  //   } catch (err) { if (mounted.current && err.name !== "AbortError") setError(err.message); }
  //   finally { controllers.current.delete(controller); uploadLock.current = false; input.value = ""; if (mounted.current) setUploading(false); }
  // }

  async function handleIndex(documentId) {
    if (indexLock.current || activeAsk.current) return;
    indexLock.current = true; setIndexingId(documentId); setError("");
    const controller = new AbortController(); controllers.current.add(controller);
    try {
      await api(`/documents/${encodeURIComponent(documentId)}/index`, { method: "POST", signal: controller.signal });
      if (mounted.current) setDocuments(previous => previous.map(doc => doc.document_id === documentId ? { ...doc, status: "indexed" } : doc));
    } catch (err) { if (mounted.current && err.name !== "AbortError") setError(err.message); }
    finally { controllers.current.delete(controller); indexLock.current = false; if (mounted.current) setIndexingId(null); }
  }

  async function askQuestion(text, retryId = null) {
    text = text.trim();

    if (
      !text ||
      text.length > 2000 ||
      !selectedId ||
      activeAsk.current ||
      indexLock.current
    ) return;

    if (
      documents.find(doc => doc.document_id === selectedId)
        ?.status !== "indexed"
    ) return;

    const id = retryId || crypto.randomUUID();

    const request = {
      id,
      documentId: selectedId,
      controller: new AbortController(),
      startedAt: performance.now(),
    };

    activeAsk.current = request;
    controllers.current.add(request.controller);
    nearBottom.current = true;

    setShowLatest(false);
    setAsking(true);
    setError("");

    const entry = {
      id,
      question: text,
      answer: "",
      sources: [],
      status: "pending",
      startedAt: request.startedAt,
      duration: 0,
      error: "",
    };

    setChat(previous =>
      retryId
        ? patchMessage(previous, id, entry)
        : [...previous, entry]
    );

    if (!retryId) setQuestion("");

    try {
      const result = await readAnswerStream(
        request.documentId,
        text,
        request.controller.signal,
        partial => {
          if (
            mounted.current &&
            activeAsk.current === request
          ) {
            setChat(previous =>
              patchMessage(previous, id, partial)
            );
          }
        }
      );

      if (
        !mounted.current ||
        activeAsk.current !== request
      ) return;

      setChat(previous =>
        patchMessage(previous, id, {
          ...result,
          status: "complete",
          duration: performance.now() - request.startedAt,
        })
      );
    } catch (error) {
      if (
        !mounted.current ||
        activeAsk.current !== request
      ) return;

      setChat(previous =>
        patchMessage(previous, id, {
          status:
            error.name === "AbortError" ? "stopped" : "error",
          error:
            error.name === "AbortError" ? "" : error.message,
          duration: performance.now() - request.startedAt,
        })
      );
    } finally {
      controllers.current.delete(request.controller);

      if (
        mounted.current &&
        activeAsk.current === request
      ) {
        activeAsk.current = null;
        setAsking(false);
        textarea.current?.focus({ preventScroll: true });
      }
    }
  }

  function stopWaiting() {
    const request = activeAsk.current;
    if (!request) return;
    activeAsk.current = null; request.controller.abort(); controllers.current.delete(request.controller);
    setChat(previous => patchMessage(previous, request.id, { status: "stopped", duration: performance.now() - request.startedAt }));
    setAsking(false); textarea.current?.focus({ preventScroll: true });
  }

  const selectedDocument = documents.find(doc => doc.document_id === selectedId);
  const selectedName = selectedDocument ? documentName(selectedDocument) : "Your document workspace";
  const visibleDocuments = documents.filter(doc => documentName(doc).toLowerCase().includes(filter.toLowerCase()));
  const readyCount = documents.filter(doc => doc.status === "indexed").length;
  const busy = asking || indexingId !== null;
  const suggestions = [
    { icon: "book", title: "Understand a topic", text: "What is this document about?" },
    { icon: "search", title: "Explore a rule", text: "What requirements or conditions are mentioned?" },
    { icon: "chat", title: "Find a detail", text: "What important dates or amounts are mentioned?" },
  ];

  return <div className="dm-app">
    {sidebarOpen && <button className="sidebar-backdrop" aria-label="Close documents" onClick={() => setSidebarOpen(false)} />}
    <aside ref={sidebar} id="document-library" className={`dm-sidebar ${sidebarOpen ? "open" : ""}`} aria-label="Document library">
      <div className="brand-row"><div className="dm-brand"><span className="brand-mark"><Icon name="sparkle" size={22} /></span>docmind<span className="brand-period">.</span></div><button className="icon-button mobile-only" onClick={() => setSidebarOpen(false)} aria-label="Close documents"><Icon name="close" /></button></div>
      <div className="sidebar-eyebrow">YOUR WORKSPACE <span>PDF CHAT</span></div>
      <button className="upload-button" onClick={() => uploadInput.current?.click()} disabled={uploading || loading || busy}><span>{uploading ? <Spinner /> : <Icon name="plus" size={18} />}{uploading ? "Uploading document…" : "Upload a PDF"}</span><span className="upload-hint">Up to 10 MB</span></button>
      <input className="sr-only" type="file" ref={uploadInput} accept=".pdf,application/pdf" onChange={handleUpload} disabled={uploading || loading} aria-label="Choose PDF" />
      {uploadMessage && <div className="upload-success" role="status"><Icon name="check" size={15} /><span>{uploadMessage}</span><button aria-label="Dismiss upload notice" onClick={() => setUploadMessage("")}><Icon name="close" size={13} /></button></div>}
      <div className="library-heading"><h2>Documents <span>{documents.length}</span></h2><button className="icon-button" aria-label="Refresh documents" disabled={loading || uploading || busy} onClick={() => setLoadVersion(value => value + 1)}><Icon name="refresh" size={15} /></button></div>
      <label className="document-filter"><Icon name="search" size={15} /><span className="sr-only">Filter documents</span><input value={filter} onChange={event => setFilter(event.target.value)} placeholder="Find a document…" /></label>
      <div className="library-list" aria-busy={loading}>
        {loading && <div className="skeleton-stack" role="status" aria-label="Loading documents"><div /><div /><div /></div>}
        {loadError && <div className="library-error" role="alert"><p>{loadError}</p><button className="text-button" disabled={loading} onClick={() => setLoadVersion(value => value + 1)}>Try loading again</button></div>}
        {!loading && !loadError && !documents.length && <div className="library-empty"><Icon name="file" size={28} /><p>Your PDFs belong here.</p><span>Upload one to get started.</span></div>}
        {!loading && documents.length > 0 && !visibleDocuments.length && <p className="muted small">No documents match your search.</p>}
        {visibleDocuments.map(doc => <div className={`library-document ${selectedId === doc.document_id ? "selected" : ""}`} key={doc.document_id}>
          <button className="document-button" title={documentName(doc)} disabled={asking || doc.status !== "indexed"} aria-pressed={selectedId === doc.document_id} onClick={() => selectDocument(doc)}><span className="file-mark"><Icon name="file" size={18} /></span><span className="document-info"><strong>{documentName(doc)}</strong><span><i className={doc.status === "indexed" ? "ready-dot" : "waiting-dot"} />{indexingId === doc.document_id ? "Preparing…" : doc.status === "indexed" ? "Ready to chat" : "Not prepared"}<b>·</b>{formatBytes(doc.size_bytes)}</span></span>{selectedId === doc.document_id && <Icon name="check" size={14} />}</button>
          {doc.status !== "indexed" && <button className="prepare-button" disabled={busy} onClick={() => handleIndex(doc.document_id)}>{indexingId === doc.document_id ? <><Spinner /> Preparing…</> : <>Prepare for chat <span>↗</span></>}</button>}
        </div>)}
      </div>
      <div className="sidebar-bottom"><span className="mini-avatar"><Icon name="file" size={17} /></span><div><strong>Your workspace</strong><span>{readyCount} ready · {documents.length} documents</span></div><span className="beta-label">BETA</span></div>
    </aside>

    <main className="dm-main">
      <header className="dm-header"><div className="header-left"><button ref={menuButton} className="icon-button mobile-only" aria-label="Open documents" aria-controls="document-library" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(true)}><Icon name="menu" /></button><div className="breadcrumb">Workspace <span>/</span> <span className="breadcrumb-current">Document chat</span></div></div><div className="header-actions"><span className="local-badge"><i />AI assistant</span><button className="quiet-button" disabled={!chat.length || asking} onClick={() => { setChat([]); setError(""); nearBottom.current = true; setShowLatest(false); }}>Clear chat</button></div></header>
      <div className="document-bar"><span className="selected-file-icon"><Icon name="file" size={17} /></span><div><strong title={selectedName}>{selectedName}</strong><span>{selectedId ? "Ask questions grounded in this PDF" : "Upload a PDF or choose one from your documents"}</span></div>{selectedId && <span className="ready-pill"><Icon name="check" size={12} /> Ready</span>}</div>
      {error && <div className="action-error" role="alert"><span>{error}</span><button className="icon-button" aria-label="Dismiss error" onClick={() => setError("")}><Icon name="close" size={16} /></button></div>}
      <div className="chat-scroll" ref={scroller} onScroll={event => { const node = event.currentTarget; nearBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 100; setShowLatest(!nearBottom.current); }}>
        {!chat.length ? <section className="dm-welcome"><div className="welcome-emblem"><Icon name="sparkle" size={32} /></div><span className="welcome-caption">A LITTLE CLARITY, ONE QUESTION AT A TIME</span><h1>Your documents.<br /><span>A whole new conversation.</span></h1><p>{selectedId ? "Your PDF is ready. Ask something specific and explore the passages behind the answer." : "Upload a PDF, ask a question, and explore the passages behind each answer."}</p><div className="welcome-suggestions">{suggestions.map(item => <button key={item.title} disabled={!selectedId || busy} onClick={() => { setQuestion(item.text); textarea.current?.focus(); }}><span className="suggestion-icon"><Icon name={item.icon} /></span><strong>{item.title}</strong><span>{item.text}</span><b>↗</b></button>)}</div><div className="welcome-footnote"><Icon name="shield" size={14} /> Ask questions. Explore the source passages.</div></section> : <div className="chat-messages">
          <div className="conversation-divider"><span />THIS DOCUMENT<span /></div>
          {chat.map(message => (
  <article key={message.id} className="chat-turn">
    <div className="user-row">
      <div className="user-bubble">
        {message.question}
      </div>
      <span className="user-avatar">You</span>
    </div>

    <div className="assistant-row">
      <span className="assistant-mark">
        <Icon name="sparkle" size={19} />
      </span>

      <div className="assistant-body">
        <div className="assistant-heading">
          <strong>DocMind</strong>
          <span>DOCUMENT ASSISTANT</span>
        </div>

        {message.answer && (
          <div className="answer-markdown">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              skipHtml
              components={{
                img: ({ alt }) => (
                  <span>[Image: {alt || "omitted"}]</span>
                ),
                a: ({ href, children }) => (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {children}
                  </a>
                ),
              }}
            >
              {message.answer}
            </ReactMarkdown>
          </div>
        )}

        {message.status === "pending" && (
          <PendingAnswer
            startedAt={message.startedAt}
            onStop={stopWaiting}
          />
        )}

        {message.status === "complete" && (
          <div className="answer-actions">
            <CopyAnswer text={message.answer} />
            <span>
              {formatDuration(message.duration)} request time
            </span>
          </div>
        )}

        {["error", "stopped"].includes(message.status) && (
          <div
            className={`message-status ${message.status}`}
            role="status"
          >
            <strong>
              {message.status === "stopped"
                ? "Stopped waiting"
                : "Request interrupted"}
            </strong>

            {message.answer && (
              <p>
                This is a partial answer; the response did not finish.
              </p>
            )}

            <p>
              {message.status === "stopped"
                ? "The browser request was cancelled. Ollama may still be processing it."
                : message.error}
            </p>

            <button
              className="text-button"
              disabled={busy}
              onClick={() =>
                askQuestion(message.question, message.id)
              }
            >
              <Icon name="refresh" size={14} />
              Retry question
            </button>
          </div>
        )}

        {message.status !== "pending" && (
          <Sources sources={message.sources} />
        )}
      </div>
    </div>
  </article>
))}
        </div>}
      </div>
      <div className="dm-composer-area">{showLatest && chat.length > 0 && <button className="jump-latest" onClick={jumpToLatest}><Icon name="down" size={14} /> Jump to latest</button>}
        <form className={`dm-composer ${asking ? "is-working" : ""}`} onSubmit={event => { event.preventDefault(); askQuestion(question); }}><label className="sr-only" htmlFor="question">Question about your PDF</label><textarea ref={textarea} id="question" rows={1} maxLength={2000} disabled={!selectedId} value={question} placeholder={selectedId ? "Ask anything about this document…" : "Select a prepared document to start…"} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); askQuestion(question); } }} /><div className="composer-bottom"><span><Icon name="file" size={13} /><span className="composer-file" title={selectedName}>{selectedId ? selectedName : "No document selected"}</span></span><div><span className="keyboard-hint">Enter to send</span><button type="submit" className="send-button" disabled={!selectedId || busy || !question.trim()} aria-label="Send question">{asking ? <Spinner /> : <Icon name="arrow" size={18} />}</button></div></div></form>
        <p className="composer-disclaimer">AI can make mistakes. Check important details in your PDF. <span>Each question is answered independently.</span></p>
      </div>
    </main>
  </div>;
}

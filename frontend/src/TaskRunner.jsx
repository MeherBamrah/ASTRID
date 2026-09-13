import React, { useState, useEffect, useRef } from "react";
import { 
  getSession, 
  signInWithGoogle, 
  signOut, 
  setGuestSession, 
  isSupabaseConfigured 
} from "./supabaseClient";
import { 
  Play, 
  Square, 
  Globe, 
  Terminal, 
  CheckCircle2, 
  AlertCircle, 
  Layers, 
  Cpu, 
  LogIn, 
  LogOut, 
  User, 
  Monitor, 
  EyeOff, 
  Send, 
  ArrowDown, 
  ArrowUp, 
  RotateCw,
  MousePointer,
  HelpCircle,
  BrainCircuit,
  Compass,
  CheckCheck
} from "lucide-react";

const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8080/ws/task";

const SAMPLE_GOALS = [
  "Search on YouTube for a channel name live insaan",
  "Search on YouTube for a channel name triggered insaan",
  "Open YouTube and check my logged-in subscriptions",
  "Take a multi-question quiz on an online quiz portal and select answers",
  "Search Python 3.12 features on python.org and read summary",
  "Open Hacker News and find the top story title"
];

export default function TaskRunner() {
  const [goal, setGoal] = useState("");
  const [session, setSession] = useState(null);
  const [isRunning, setIsRunning] = useState(false);
  const [isHeadless, setIsHeadless] = useState(false); // Default: False (Desktop window + interactive)
  const [logs, setLogs] = useState([]);
  const [currentPlan, setCurrentPlan] = useState([]);
  const [activeAction, setActiveAction] = useState(null);
  const [latestScreenshot, setLatestScreenshot] = useState(null);
  const [currentUrl, setCurrentUrl] = useState("https://www.youtube.com");
  const [manualUrlInput, setManualUrlInput] = useState("https://www.youtube.com");
  const [manualTextInput, setManualTextInput] = useState("");
  const [verificationResult, setVerificationResult] = useState(null);
  const [wsConnected, setWsConnected] = useState(false);

  const wsRef = useRef(null);
  const logEndRef = useRef(null);

  // Auto-connect persistent websocket on mount for interactive browsing
  const connectWebSocket = async () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;

    let curSession = await getSession();
    if (!curSession) {
      curSession = setGuestSession();
      setSession(curSession);
    } else {
      setSession(curSession);
    }

    const token = curSession.access_token;
    const ws = new WebSocket(`${WS_URL}?token=${encodeURIComponent(token)}`);
    wsRef.current = ws;

    ws.onopen = () => {
      setWsConnected(true);
      ws.send(JSON.stringify({ event: "ping" }));
    };

    ws.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data);
        const { event, data } = parsed;

        if (event === "screenshot") {
          setLatestScreenshot(data);
        } else if (event === "plan") {
          setCurrentPlan(data || []);
          setLogs((prev) => [...prev, parsed]);
        } else if (event === "action") {
          setActiveAction(data?.action || null);
          if (data?.action?.action === "navigate" && data?.action?.target) {
            setCurrentUrl(data.action.target);
            setManualUrlInput(data.action.target);
          }
          setLogs((prev) => [...prev, parsed]);
        } else if (event === "verified") {
          setVerificationResult(data);
          setLogs((prev) => [...prev, parsed]);
        } else if (event === "complete" || event === "error") {
          setIsRunning(false);
          setLogs((prev) => [...prev, parsed]);
        } else if (event) {
          setLogs((prev) => [...prev, parsed]);
        }
      } catch (e) {
        console.error("WS message parse error:", e);
      }
    };

    ws.onerror = () => {
      setWsConnected(false);
      setIsRunning(false);
    };

    ws.onclose = () => {
      setWsConnected(false);
      setIsRunning(false);
    };
  };

  useEffect(() => {
    connectWebSocket();
    return () => {
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const handleLoginGoogle = async () => {
    const res = await signInWithGoogle();
    if (res?.error) alert(res.error.message);
  };

  const handleGuestLogin = () => {
    const guest = setGuestSession();
    setSession(guest);
  };

  const handleLogout = async () => {
    await signOut();
    setSession(null);
  };

  // 1. RUN AUTONOMOUS AGENT TASK
  const startTask = (customGoal) => {
    const taskGoal = (customGoal || goal).trim();
    if (!taskGoal) return;

    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      connectWebSocket().then(() => {
        setTimeout(() => startTask(taskGoal), 500);
      });
      return;
    }

    setIsRunning(true);
    setLogs([]);
    setCurrentPlan([]);
    setActiveAction(null);
    setVerificationResult(null);

    wsRef.current.send(JSON.stringify({ 
      event: "goal",
      goal: taskGoal, 
      headless: isHeadless 
    }));
  };

  const stopTask = () => {
    if (wsRef.current) {
      wsRef.current.send(JSON.stringify({ event: "stop" }));
    }
    setIsRunning(false);
  };

  // 2. MANUAL CLICK DIRECTLY ON THE LIVE VIEWPORT
  const handleViewportClick = (e) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const scaleX = 1280 / rect.width;
    const scaleY = 720 / rect.height;
    const x = Math.round((e.clientX - rect.left) * scaleX);
    const y = Math.round((e.clientY - rect.top) * scaleY);

    wsRef.current.send(JSON.stringify({ event: "manual_click", x, y }));
  };

  // 3. MANUAL NAVIGATION
  const handleManualNavigate = (e) => {
    e?.preventDefault();
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    setCurrentUrl(manualUrlInput);
    wsRef.current.send(JSON.stringify({ event: "manual_navigate", url: manualUrlInput }));
  };

  // 4. MANUAL TEXT SEND
  const handleManualSendText = (pressEnter = true) => {
    if (!manualTextInput || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ 
      event: "manual_type", 
      text: manualTextInput, 
      press_enter: pressEnter 
    }));
    setManualTextInput("");
  };

  // 5. MANUAL SCROLL
  const handleManualScroll = (dy) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ event: "manual_scroll", dy }));
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      {/* Header */}
      <header
        style={{
          height: "64px",
          borderBottom: "1px solid var(--border-color)",
          backgroundColor: "var(--bg-secondary)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 1.5rem",
          zIndex: 10,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <div
            style={{
              width: "36px",
              height: "36px",
              borderRadius: "10px",
              background: "linear-gradient(135deg, #6366f1 0%, #06b6d4 100%)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: "0 0 15px rgba(99, 102, 241, 0.4)",
            }}
          >
            <Cpu size={20} color="#fff" />
          </div>
          <div>
            <h1 style={{ fontSize: "1.15rem", fontWeight: 700, letterSpacing: "-0.02em" }}>
              ASTRID <span style={{ color: "var(--accent-cyan)", fontSize: "0.8rem", fontWeight: 500 }}>v2.0</span>
            </h1>
            <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "-2px" }}>
              Interactive Multi-Agent Browser with Direct Click Takeover & Persistent Logins
            </p>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <button
            onClick={() => setIsHeadless(!isHeadless)}
            disabled={isRunning}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.45rem",
              padding: "0.4rem 0.8rem",
              borderRadius: "999px",
              backgroundColor: isHeadless ? "rgba(255,255,255,0.05)" : "rgba(99, 102, 241, 0.2)",
              border: `1px solid ${isHeadless ? "var(--border-color)" : "var(--accent-primary)"}`,
              color: isHeadless ? "var(--text-muted)" : "var(--text-primary)",
              fontSize: "0.78rem",
              fontWeight: 600,
              cursor: isRunning ? "not-allowed" : "pointer",
            }}
            title="Toggle between opening a visible desktop browser window or headless stream"
          >
            {isHeadless ? <EyeOff size={14} /> : <Monitor size={14} color="var(--accent-cyan)" />}
            <span>{isHeadless ? "Headless Mode" : "Desktop Window Active"}</span>
          </button>

          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
              padding: "0.35rem 0.75rem",
              borderRadius: "999px",
              backgroundColor: "var(--bg-glass)",
              border: "1px solid var(--border-color)",
              fontSize: "0.8rem",
            }}
          >
            <span
              style={{
                width: "8px",
                height: "8px",
                borderRadius: "50%",
                backgroundColor: wsConnected ? "var(--accent-emerald)" : "var(--accent-amber)",
                boxShadow: wsConnected ? "0 0 8px var(--accent-emerald)" : "none",
              }}
            />
            <span style={{ color: "var(--text-secondary)" }}>
              {wsConnected ? "Interactive Online" : "Connecting..."}
            </span>
          </div>

          {session ? (
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <img
                  src={session.user?.user_metadata?.avatar_url || "https://api.dicebear.com/7.x/bottts/svg?seed=Astrid"}
                  alt="avatar"
                  style={{ width: "32px", height: "32px", borderRadius: "50%", border: "1px solid var(--border-color)" }}
                />
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: "0.8rem", fontWeight: 600 }}>
                    {session.user?.user_metadata?.full_name || session.user?.email || "Dev User"}
                  </div>
                  <div style={{ fontSize: "0.68rem", color: "var(--accent-cyan)" }}>
                    Persistent Session
                  </div>
                </div>
              </div>
              <button
                onClick={handleLogout}
                style={{
                  padding: "0.4rem 0.6rem",
                  borderRadius: "var(--radius-sm)",
                  backgroundColor: "rgba(244, 63, 94, 0.15)",
                  color: "var(--accent-rose)",
                  display: "flex",
                  alignItems: "center",
                  fontSize: "0.75rem",
                }}
                title="Sign out"
              >
                <LogOut size={14} />
              </button>
            </div>
          ) : (
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button
                onClick={handleGuestLogin}
                style={{
                  padding: "0.45rem 0.9rem",
                  borderRadius: "var(--radius-sm)",
                  backgroundColor: "var(--bg-glass)",
                  border: "1px solid var(--border-color)",
                  color: "var(--text-primary)",
                  fontSize: "0.8rem",
                  display: "flex",
                  alignItems: "center",
                  gap: "0.4rem",
                }}
              >
                <User size={14} /> Guest Dev Mode
              </button>
              <button
                onClick={handleLoginGoogle}
                style={{
                  padding: "0.45rem 0.9rem",
                  borderRadius: "var(--radius-sm)",
                  background: "linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)",
                  color: "#fff",
                  fontSize: "0.8rem",
                  fontWeight: 600,
                  display: "flex",
                  alignItems: "center",
                  gap: "0.4rem",
                }}
              >
                <LogIn size={14} /> Google Sign In
              </button>
            </div>
          )}
        </div>
      </header>

      {/* Main Split Interface */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        
        {/* LEFT COLUMN: Controls, Multi-Agent Activity & Memory */}
        <section
          style={{
            width: "50%",
            borderRight: "1px solid var(--border-color)",
            display: "flex",
            flexDirection: "column",
            backgroundColor: "var(--bg-primary)",
          }}
        >
          {/* Goal Input Card */}
          <div
            style={{
              padding: "1.25rem",
              borderBottom: "1px solid var(--border-color)",
              backgroundColor: "var(--bg-secondary)",
            }}
          >
            <div style={{ display: "flex", gap: "0.75rem" }}>
              <input
                type="text"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !isRunning && startTask()}
                disabled={isRunning}
                placeholder="Instruct Astrid (e.g. Search on youtube for a channel name live insaan)"
                style={{
                  flex: 1,
                  padding: "0.75rem 1rem",
                  borderRadius: "var(--radius-md)",
                  backgroundColor: "var(--bg-primary)",
                  color: "var(--text-primary)",
                  fontSize: "0.9rem",
                }}
              />
              {isRunning ? (
                <button
                  onClick={stopTask}
                  style={{
                    padding: "0.75rem 1.25rem",
                    borderRadius: "var(--radius-md)",
                    backgroundColor: "var(--accent-rose)",
                    color: "#fff",
                    fontWeight: 600,
                    display: "flex",
                    alignItems: "center",
                    gap: "0.4rem",
                  }}
                >
                  <Square size={16} /> Stop
                </button>
              ) : (
                <button
                  onClick={() => startTask()}
                  disabled={!goal.trim()}
                  style={{
                    padding: "0.75rem 1.4rem",
                    borderRadius: "var(--radius-md)",
                    background: goal.trim()
                      ? "linear-gradient(135deg, #6366f1 0%, #06b6d4 100%)"
                      : "rgba(255,255,255,0.06)",
                    color: goal.trim() ? "#fff" : "var(--text-muted)",
                    fontWeight: 600,
                    display: "flex",
                    alignItems: "center",
                    gap: "0.4rem",
                    boxShadow: goal.trim() ? "var(--shadow-glow)" : "none",
                  }}
                >
                  <Play size={16} /> Execute
                </button>
              )}
            </div>

            {/* Suggestions */}
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginTop: "0.75rem" }}>
              <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", alignSelf: "center", marginRight: "0.25rem" }}>
                Suggestions:
              </span>
              {SAMPLE_GOALS.map((sg, idx) => (
                <button
                  key={idx}
                  onClick={() => {
                    setGoal(sg);
                    if (!isRunning) startTask(sg);
                  }}
                  disabled={isRunning}
                  style={{
                    padding: "0.25rem 0.6rem",
                    borderRadius: "999px",
                    backgroundColor: "var(--bg-glass)",
                    border: "1px solid var(--border-color)",
                    color: "var(--text-secondary)",
                    fontSize: "0.72rem",
                  }}
                >
                  {sg.length > 34 ? sg.slice(0, 32) + "..." : sg}
                </button>
              ))}
            </div>

            {/* Memory Info Bar */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginTop: "0.75rem",
                padding: "0.35rem 0.6rem",
                borderRadius: "var(--radius-sm)",
                backgroundColor: "rgba(0,0,0,0.25)",
                fontSize: "0.7rem",
                color: "var(--text-muted)",
              }}
            >
              <div style={{ display: "flex", gap: "0.75rem" }}>
                <span style={{ display: "flex", alignItems: "center", gap: "0.25rem", color: "var(--accent-cyan)" }}>
                  <BrainCircuit size={12} /> Orchestrator Memory
                </span>
                <span style={{ display: "flex", alignItems: "center", gap: "0.25rem", color: "var(--accent-emerald)" }}>
                  <Compass size={12} /> Planner Memory
                </span>
                <span style={{ display: "flex", alignItems: "center", gap: "0.25rem", color: "var(--accent-amber)" }}>
                  <CheckCheck size={12} /> Verifier Memory
                </span>
              </div>
              <span style={{ color: "var(--accent-primary)", fontWeight: 500 }}>
                🌐 Global Memory Active
              </span>
            </div>
          </div>

          {/* Subtasks Tracker */}
          {currentPlan.length > 0 && (
            <div
              style={{
                padding: "0.75rem 1.25rem",
                backgroundColor: "rgba(99, 102, 241, 0.08)",
                borderBottom: "1px solid var(--border-color)",
                display: "flex",
                flexDirection: "column",
                gap: "0.4rem",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.75rem", color: "var(--accent-cyan)", fontWeight: 600 }}>
                <Layers size={14} /> AUTONOMOUS SUBTASKS ({currentPlan.length} steps)
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
                {currentPlan.map((step, idx) => (
                  <div
                    key={idx}
                    style={{
                      fontSize: "0.78rem",
                      color: "var(--text-secondary)",
                      display: "flex",
                      alignItems: "flex-start",
                      gap: "0.4rem",
                    }}
                  >
                    <span style={{ color: "var(--accent-primary)", fontFamily: "var(--font-mono)", fontWeight: 600 }}>
                      {idx + 1}.
                    </span>
                    <span>{step}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Agent Activity Stream */}
          <div
            style={{
              flex: 1,
              padding: "1.25rem",
              overflowY: "auto",
              display: "flex",
              flexDirection: "column",
              gap: "0.75rem",
            }}
          >
            {logs.length === 0 ? (
              <div
                style={{
                  height: "100%",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "var(--text-muted)",
                  textAlign: "center",
                  gap: "0.75rem",
                  padding: "2rem",
                }}
              >
                <div
                  style={{
                    width: "56px",
                    height: "56px",
                    borderRadius: "50%",
                    backgroundColor: "var(--bg-glass)",
                    border: "1px dashed var(--border-color)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Terminal size={24} color="var(--text-muted)" />
                </div>
                <div>
                  <h3 style={{ fontSize: "0.95rem", color: "var(--text-secondary)", fontWeight: 600 }}>
                    Astrid Engine Ready
                  </h3>
                  <p style={{ fontSize: "0.8rem", maxWidth: "340px", marginTop: "0.25rem" }}>
                    You can instruct Astrid on the left, or click & type directly on the live browser on the right!
                  </p>
                </div>
              </div>
            ) : (
              logs.map((entry, idx) => {
                const { event, data } = entry;
                return (
                  <div
                    key={idx}
                    className="animate-slide-in"
                    style={{
                      padding: "0.75rem 1rem",
                      borderRadius: "var(--radius-md)",
                      backgroundColor: "var(--bg-card)",
                      border: "1px solid var(--border-color)",
                      display: "flex",
                      flexDirection: "column",
                      gap: "0.35rem",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <span
                        style={{
                          fontSize: "0.7rem",
                          fontWeight: 700,
                          textTransform: "uppercase",
                          letterSpacing: "0.05em",
                          padding: "0.15rem 0.5rem",
                          borderRadius: "4px",
                          backgroundColor:
                            event === "action"
                              ? "rgba(6, 182, 212, 0.15)"
                              : event === "verified"
                              ? "rgba(16, 185, 129, 0.15)"
                              : event === "error"
                              ? "rgba(244, 63, 94, 0.15)"
                              : "rgba(99, 102, 241, 0.15)",
                          color:
                            event === "action"
                              ? "var(--accent-cyan)"
                              : event === "verified"
                              ? "var(--accent-emerald)"
                              : event === "error"
                              ? "var(--accent-rose)"
                              : "var(--accent-primary)",
                        }}
                      >
                        {event}
                      </span>
                      <span style={{ fontSize: "0.68rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                        #{idx + 1}
                      </span>
                    </div>

                    <div style={{ fontSize: "0.85rem", lineHeight: "1.4" }}>
                      {event === "action" ? (
                        <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
                          <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                            Step {data?.step_num || 1}: {data?.step}
                          </div>
                          <div
                            style={{
                              fontFamily: "var(--font-mono)",
                              fontSize: "0.78rem",
                              padding: "0.4rem 0.6rem",
                              borderRadius: "var(--radius-sm)",
                              backgroundColor: "rgba(0,0,0,0.3)",
                              color: "var(--accent-cyan)",
                            }}
                          >
                            action: {data?.action?.action} | target: {data?.action?.target || "none"} {data?.action?.value ? `| value: "${data.action.value}"` : ""}
                          </div>
                        </div>
                      ) : event === "verified" ? (
                        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                          {data?.verified ? (
                            <CheckCircle2 size={18} color="var(--accent-emerald)" />
                          ) : (
                            <AlertCircle size={18} color="var(--accent-rose)" />
                          )}
                          <div>
                            <div style={{ fontWeight: 600, color: data?.verified ? "var(--accent-emerald)" : "var(--accent-rose)" }}>
                              {data?.verified ? "Goal Verified" : "Verification Incomplete"}
                            </div>
                            <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>
                              {data?.reason}
                            </div>
                          </div>
                        </div>
                      ) : event === "screenshot" ? (
                        <span style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
                          📸 Live frame captured & streamed to monitor
                        </span>
                      ) : typeof data === "string" ? (
                        <span style={{ color: "var(--text-secondary)" }}>{data}</span>
                      ) : (
                        <pre
                          style={{
                            fontFamily: "var(--font-mono)",
                            fontSize: "0.75rem",
                            whiteSpace: "pre-wrap",
                            color: "var(--text-secondary)",
                          }}
                        >
                          {JSON.stringify(data, null, 2)}
                        </pre>
                      )}
                    </div>
                  </div>
                );
              })
            )}
            <div ref={logEndRef} />
          </div>
        </section>

        {/* RIGHT COLUMN: Interactive Browser Viewport & Direct Controls */}
        <section
          style={{
            width: "50%",
            display: "flex",
            flexDirection: "column",
            backgroundColor: "var(--bg-secondary)",
          }}
        >
          {/* Interactive URL Bar Header */}
          <form
            onSubmit={handleManualNavigate}
            style={{
              padding: "0.6rem 1rem",
              borderBottom: "1px solid var(--border-color)",
              backgroundColor: "rgba(10, 13, 20, 0.9)",
              display: "flex",
              alignItems: "center",
              gap: "0.75rem",
            }}
          >
            <div style={{ display: "flex", gap: "6px" }}>
              <div style={{ width: "10px", height: "10px", borderRadius: "50%", backgroundColor: "#ef4444" }} />
              <div style={{ width: "10px", height: "10px", borderRadius: "50%", backgroundColor: "#f59e0b" }} />
              <div style={{ width: "10px", height: "10px", borderRadius: "50%", backgroundColor: "#10b981" }} />
            </div>

            {/* Editable URL Input */}
            <div
              style={{
                flex: 1,
                padding: "0.3rem 0.6rem",
                borderRadius: "var(--radius-sm)",
                backgroundColor: "rgba(255,255,255,0.05)",
                border: "1px solid var(--border-color)",
                display: "flex",
                alignItems: "center",
                gap: "0.4rem",
              }}
            >
              <Globe size={13} color="var(--accent-cyan)" />
              <input
                type="text"
                value={manualUrlInput}
                onChange={(e) => setManualUrlInput(e.target.value)}
                placeholder="Enter URL to navigate manually..."
                style={{
                  flex: 1,
                  background: "transparent",
                  border: "none",
                  color: "var(--text-primary)",
                  fontSize: "0.78rem",
                  fontFamily: "var(--font-mono)",
                }}
              />
            </div>

            <button
              type="submit"
              style={{
                padding: "0.3rem 0.6rem",
                borderRadius: "var(--radius-sm)",
                backgroundColor: "rgba(99, 102, 241, 0.2)",
                border: "1px solid var(--accent-primary)",
                color: "var(--text-primary)",
                fontSize: "0.72rem",
                fontWeight: 600,
              }}
              title="Navigate to URL"
            >
              Go
            </button>

            <button
              type="button"
              onClick={() => handleManualScroll(-400)}
              style={{ padding: "0.3rem", borderRadius: "var(--radius-sm)", backgroundColor: "var(--bg-glass)", color: "var(--text-muted)" }}
              title="Scroll Up"
            >
              <ArrowUp size={14} />
            </button>
            <button
              type="button"
              onClick={() => handleManualScroll(400)}
              style={{ padding: "0.3rem", borderRadius: "var(--radius-sm)", backgroundColor: "var(--bg-glass)", color: "var(--text-muted)" }}
              title="Scroll Down"
            >
              <ArrowDown size={14} />
            </button>
          </form>

          {/* Viewport Canvas (CLICK TO INTERACT) */}
          <div
            style={{
              flex: 1,
              padding: "0.75rem",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              overflow: "hidden",
              position: "relative",
              backgroundColor: "#05070a",
            }}
          >
            {latestScreenshot ? (
              <div
                style={{
                  width: "100%",
                  height: "100%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  position: "relative",
                  cursor: "crosshair",
                }}
                onClick={handleViewportClick}
                title="Click anywhere on the web page to click that element!"
              >
                <img
                  src={latestScreenshot.startsWith("data:") ? latestScreenshot : `data:image/jpeg;base64,${latestScreenshot}`}
                  alt="Playwright interactive viewport"
                  style={{
                    maxWidth: "100%",
                    maxHeight: "100%",
                    objectFit: "contain",
                    borderRadius: "var(--radius-sm)",
                    boxShadow: "0 10px 30px rgba(0,0,0,0.5)",
                    border: "1px solid var(--border-color)",
                  }}
                />
                <div
                  style={{
                    position: "absolute",
                    top: "12px",
                    left: "12px",
                    padding: "0.25rem 0.5rem",
                    borderRadius: "4px",
                    backgroundColor: "rgba(0,0,0,0.75)",
                    backdropFilter: "blur(6px)",
                    fontSize: "0.68rem",
                    color: "var(--accent-emerald)",
                    fontFamily: "var(--font-mono)",
                    border: "1px solid rgba(16, 185, 129, 0.4)",
                    display: "flex",
                    alignItems: "center",
                    gap: "0.3rem",
                  }}
                >
                  <MousePointer size={11} /> Click-to-Interact Active
                </div>
              </div>
            ) : (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "var(--text-muted)",
                  gap: "0.75rem",
                  textAlign: "center",
                }}
              >
                <div
                  style={{
                    width: "64px",
                    height: "64px",
                    borderRadius: "16px",
                    backgroundColor: "rgba(255,255,255,0.03)",
                    border: "1px solid var(--border-color)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <MousePointer size={28} color="var(--text-muted)" />
                </div>
                <div>
                  <div style={{ fontSize: "0.9rem", color: "var(--text-secondary)", fontWeight: 600 }}>
                    Direct Interactive Browser
                  </div>
                  <div style={{ fontSize: "0.78rem", maxWidth: "300px", marginTop: "0.25rem" }}>
                    Clicking on the page sends real clicks. Type a URL above or enter a task to begin.
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Manual Input Bar: Type & Send directly to active web page element */}
          <div
            style={{
              padding: "0.6rem 1rem",
              borderTop: "1px solid var(--border-color)",
              backgroundColor: "var(--bg-secondary)",
              display: "flex",
              alignItems: "center",
              gap: "0.6rem",
            }}
          >
            <input
              type="text"
              value={manualTextInput}
              onChange={(e) => setManualTextInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleManualSendText(true)}
              placeholder="Type directly into page (e.g. search query, login, answer)..."
              style={{
                flex: 1,
                padding: "0.4rem 0.75rem",
                borderRadius: "var(--radius-sm)",
                backgroundColor: "var(--bg-primary)",
                color: "var(--text-primary)",
                fontSize: "0.8rem",
              }}
            />
            <button
              onClick={() => handleManualSendText(false)}
              style={{
                padding: "0.4rem 0.8rem",
                borderRadius: "var(--radius-sm)",
                backgroundColor: "var(--bg-glass)",
                border: "1px solid var(--border-color)",
                color: "var(--text-secondary)",
                fontSize: "0.75rem",
                fontWeight: 600,
              }}
              title="Type text without pressing Enter"
            >
              Type
            </button>
            <button
              onClick={() => handleManualSendText(true)}
              style={{
                padding: "0.4rem 0.8rem",
                borderRadius: "var(--radius-sm)",
                background: "linear-gradient(135deg, #6366f1 0%, #06b6d4 100%)",
                color: "#fff",
                fontSize: "0.75rem",
                fontWeight: 600,
                display: "flex",
                alignItems: "center",
                gap: "0.3rem",
              }}
              title="Type text and press Enter"
            >
              <Send size={12} /> Type & Enter
            </button>
          </div>
        </section>

      </div>
    </div>
  );
}

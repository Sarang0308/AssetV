// Right column: the conversation with the AI agents.
import { useEffect, useRef, useState } from "react";
import { askAgents } from "../api.js";
import Message from "./Message.jsx";

const EXAMPLE_QUESTIONS = [
  "Am I financially healthy?",
  "Show my income and expenses for the last 6 months",
  "Where does my money go?",
  "Show my assets as a bar chart, excluding real estate",
  "Compare my spending in 2025 and 2026",
  "Are there any unusual transactions?",
  "What if I pay off my credit card and cut shopping by 30%?",
  "What should I do next?",
];

export default function Chat() {
  // Each message looks like:
  //   { role: "user", text }  or
  //   { role: "assistant", text, charts: [], steps: [], error, usage, loading }
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState(null); // lets the AI remember earlier questions
  const bottomRef = useRef(null);

  // Scroll to the newest message whenever the list changes.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Update the last message (the assistant reply that is being streamed in).
  function updateReply(change) {
    setMessages((all) => {
      const copy = [...all];
      const last = copy[copy.length - 1];
      copy[copy.length - 1] = { ...last, ...change(last) };
      return copy;
    });
  }

  async function send(question) {
    question = question.trim();
    if (!question || busy) return;

    setInput("");
    setBusy(true);
    setMessages((all) => [
      ...all,
      { role: "user", text: question },
      { role: "assistant", text: "", charts: [], steps: [], loading: true },
    ]);

    try {
      await askAgents(question, sessionId, (event) => {
        // The backend streams small "events"; each one updates the reply a little.
        if (event.type === "session") setSessionId(event.session_id);
        if (event.type === "agent" && event.status === "start")
          updateReply((m) => ({ steps: [...m.steps, { agent: event.agent, task: event.task, tools: [] }] }));
        if (event.type === "tool_call")
          updateReply((m) => ({
            steps: m.steps.map((s) => (s.agent === event.agent ? { ...s, tools: [...s.tools, event.name] } : s)),
          }));
        if (event.type === "chart") updateReply((m) => ({ charts: [...m.charts, event.spec] }));
        if (event.type === "text") updateReply((m) => ({ text: m.text ? m.text + "\n\n" + event.text : event.text }));
        if (event.type === "usage") updateReply(() => ({ usage: event }));
        if (event.type === "error") updateReply(() => ({ error: event.message }));
      });
    } catch (err) {
      updateReply(() => ({ error: "Could not reach the server: " + err.message }));
    }

    updateReply(() => ({ loading: false }));
    setBusy(false);
  }

  function newChat() {
    setMessages([]);
    setSessionId(null);
  }

  return (
    <main className="chat">
      <header className="chat-header">
        <div>
          <h1>Ask about your money</h1>
          <p className="muted">
            Ask in plain English. AI agents look up the numbers, and every figure is calculated from your data —
            the AI never guesses numbers.
          </p>
        </div>
        {messages.length > 0 && (
          <button className="button-secondary" onClick={newChat}>New chat</button>
        )}
      </header>

      <div className="messages">
        {messages.length === 0 && (
          <div className="welcome">
            <p className="muted">Not sure where to start? Click a question:</p>
            <div className="examples">
              {EXAMPLE_QUESTIONS.map((q) => (
                <button key={q} className="example" onClick={() => send(q)}>{q}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((message, index) => (
          <Message key={index} message={message} sessionId={sessionId} />
        ))}
        <div ref={bottomRef} />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault(); // stop the browser from reloading the page
          send(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a question, e.g. How much did I save last month?"
          disabled={busy}
        />
        <button className="button-primary" type="submit" disabled={busy || !input.trim()}>
          {busy ? "Thinking…" : "Ask"}
        </button>
      </form>
    </main>
  );
}

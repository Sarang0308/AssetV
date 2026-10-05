// "Download report" button + dropdown panel in the chat header.
// The panel is anchored to the button (not the page), so it always opens right below it.
import { useEffect, useRef, useState } from "react";
import { downloadReport, fetchOverview } from "../api.js";

export default function ReportPanel() {
  const [open, setOpen] = useState(false);
  const [periods, setPeriods] = useState(null);
  const [type, setType] = useState("ca_pack");
  const [fy, setFy] = useState("2025-26");
  const [month, setMonth] = useState("");
  const [name, setName] = useState("");
  const [pan, setPan] = useState("");
  const [hideDetails, setHideDetails] = useState(false);
  const [status, setStatus] = useState({ text: "", error: false });
  const [busy, setBusy] = useState(false);
  const wrapRef = useRef(null);

  // FY options and month limits come from the backend so they always match the data.
  useEffect(() => {
    fetchOverview().then((o) => {
      const p = o.report_periods;
      if (!p) return;
      setPeriods(p);
      setFy((p.financial_years.find((f) => !f.partial) || p.financial_years[0]).fy);
      setMonth(p.last_complete_month);
    });
  }, []);

  // Close on outside click or Escape. The PAN is cleared whenever the panel closes.
  useEffect(() => {
    if (!open) return;
    const onClick = (e) => { if (wrapRef.current && !wrapRef.current.contains(e.target)) close(); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onClick); document.removeEventListener("keydown", onKey); };
  }, [open]);

  function close() {
    setOpen(false);
    setPan("");
    setStatus({ text: "", error: false });
  }

  async function download(format) {
    const params = { type, format, hide_details: String(hideDetails) };
    if (type === "ca_pack") {
      params.fy = fy;
      if (name.trim()) params.name = name.trim();
      if (pan.trim()) params.pan = pan.trim();
    } else if (month) {
      params.month = month;
    }
    setBusy(true);
    setStatus({ text: "Preparing report…", error: false });
    try {
      await downloadReport(params);
      setStatus({ text: "Report downloaded.", error: false });
      setPan("");
    } catch (err) {
      setStatus({ text: err.message, error: true });
    } finally {
      setBusy(false);
    }
  }

  const caPack = type === "ca_pack";
  return (
    <div className="report-wrap" ref={wrapRef}>
      <button className="button-secondary" aria-expanded={open} onClick={() => (open ? close() : setOpen(true))}>
        Download report
      </button>
      {open && (
        <section className="report-panel" aria-label="Download report">
          <div className="report-panel-head">
            <h2>Download report</h2>
            <button className="report-close" aria-label="Close" onClick={close}>×</button>
          </div>

          <label>Report type
            <select value={type} onChange={(e) => setType(e.target.value)}>
              <option value="ca_pack">Tax / CA pack</option>
              <option value="monthly">Monthly review</option>
            </select>
          </label>

          {caPack ? (
            <>
              <label>Financial year
                <select value={fy} onChange={(e) => setFy(e.target.value)}>
                  {(periods?.financial_years || [{ fy: "2025-26", partial: false }]).map((f) => (
                    <option key={f.fy} value={f.fy}>FY {f.fy}{f.partial ? " · partial period" : ""}</option>
                  ))}
                </select>
              </label>
              <label>Name (optional)
                <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
              </label>
              <label>PAN (optional)
                <input value={pan} onChange={(e) => setPan(e.target.value.toUpperCase())} autoComplete="off"
                       spellCheck={false} maxLength={10} placeholder="AAAAA9999A" />
              </label>
            </>
          ) : (
            <label>Month
              <input type="month" value={month} min={periods?.first_month} max={periods?.last_complete_month}
                     onChange={(e) => setMonth(e.target.value)} />
            </label>
          )}

          <label className="report-toggle">
            <input type="checkbox" checked={hideDetails} onChange={(e) => setHideDetails(e.target.checked)} />
            Hide transaction details
          </label>

          <div className="report-actions">
            <button className="button-primary" disabled={busy} onClick={() => download("pdf")}>Download PDF</button>
            {caPack && (
              <button className="button-secondary" disabled={busy} onClick={() => download("xlsx")}>Download Excel</button>
            )}
          </div>
          <p className={`report-status${status.error ? " error" : ""}`} role="status" aria-live="polite">{status.text}</p>
        </section>
      )}
    </div>
  );
}

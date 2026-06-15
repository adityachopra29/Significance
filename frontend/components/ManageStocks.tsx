"use client";

import { useEffect, useState } from "react";
import { CompanyAdmin, addCompany, deleteCompany, getCompanies } from "@/lib/api";

export default function ManageStocks({
  onClose,
  onChanged,
}: {
  onClose: () => void;
  onChanged: () => void;
}) {
  const [companies, setCompanies] = useState<CompanyAdmin[]>([]);
  const [search, setSearch] = useState("");
  const [scrip, setScrip] = useState("");
  const [symbol, setSymbol] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const refresh = () => {
    getCompanies().then(setCompanies).catch((e) => setMsg(String(e)));
  };

  useEffect(refresh, []);

  const onAdd = async () => {
    if (!scrip && !symbol) {
      setMsg("Enter a BSE scrip code or NSE symbol.");
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const c = await addCompany({
        scrip_code: scrip || undefined,
        nse_symbol: symbol || undefined,
      });
      const cached = c.backfill_cached ?? 0;
      const fresh = c.backfill_new ?? 0;
      const msgParts = [`Added ${c.name}.`];
      if (fresh > 0) msgParts.push(`${fresh} new filing(s) queued for analysis.`);
      if (cached > 0) msgParts.push(`${cached} already cached (not re-analyzed).`);
      if (fresh === 0 && cached === 0) msgParts.push("No filings in backfill window.");
      setMsg(msgParts.join(" "));
      setScrip("");
      setSymbol("");
      refresh();
      onChanged();
    } catch (e) {
      setMsg(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  const onRemove = async (c: CompanyAdmin) => {
    if (!confirm(`Stop monitoring ${c.name}?`)) return;
    await deleteCompany(c.id, false);
    refresh();
    onChanged();
  };

  const filtered = companies.filter(
    (c) =>
      !search ||
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      c.bse_scrip_code.includes(search) ||
      (c.nse_symbol || "").toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>
          Close
        </button>
        <h2>Monitored stocks ({companies.length})</h2>

        <div className="section">
          <h3>Add a stock</h3>
          <div className="add-row">
            <input
              placeholder="BSE scrip code (e.g. 500325)"
              value={scrip}
              onChange={(e) => setScrip(e.target.value)}
            />
            <span className="or">or</span>
            <input
              placeholder="NSE symbol (e.g. RELIANCE)"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            />
            <button className="btn" disabled={busy} onClick={onAdd}>
              {busy ? "Adding…" : "Add"}
            </button>
          </div>
          {msg && <div className="msg">{msg}</div>}
        </div>

        <div className="section">
          <h3>Current list</h3>
          <input
            className="search"
            placeholder="Filter by name, NSE symbol, or BSE code…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="stock-list">
            {filtered.map((c) => (
              <div className="stock-row" key={c.id}>
                <div>
                  <div className="stock-name">{c.name}</div>
                  <div className="stock-sub">
                    {c.nse_symbol || c.bse_scrip_code} · {c.sector || "—"} ·{" "}
                    {c.analyzed_count}/{c.announcement_count} analyzed
                  </div>
                </div>
                <button className="remove" onClick={() => onRemove(c)}>
                  Remove
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

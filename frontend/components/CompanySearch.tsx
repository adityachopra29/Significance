"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CompanyAdmin, getCompanies } from "@/lib/api";

function companyLabel(c: CompanyAdmin): string {
  const parts = [c.name];
  if (c.nse_symbol) parts.push(c.nse_symbol);
  if (c.bse_scrip_code) parts.push(c.bse_scrip_code);
  return parts.join(" · ");
}

export default function CompanySearch({
  value,
  onChange,
  placeholder = "Search name or code…",
}: {
  value: string;
  onChange: (companyId: string) => void;
  placeholder?: string;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<CompanyAdmin[]>([]);
  const [selected, setSelected] = useState<CompanyAdmin | undefined>();
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    if (!value) setSelected(undefined);
  }, [value]);

  useEffect(() => {
    const q = query.trim();
    if (!open || q.length < 1) {
      setOptions([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    let cancelled = false;
    const timer = window.setTimeout(() => {
      getCompanies(q)
        .then((rows) => {
          if (!cancelled) setOptions(rows.slice(0, 40));
        })
        .catch(() => {
          if (!cancelled) setOptions([]);
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, query]);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
        if (selected) setQuery("");
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [selected]);

  const pick = (c: CompanyAdmin) => {
    setSelected(c);
    onChange(String(c.id));
    setQuery("");
    setOpen(false);
  };

  const clear = () => {
    setSelected(undefined);
    onChange("");
    setQuery("");
    setOpen(false);
    setOptions([]);
  };

  const displayValue = open ? query : selected ? companyLabel(selected) : query;
  const showHint = open && query.trim().length < 1;

  const listContent = useMemo(() => {
    if (showHint) {
      return <li className="company-search-empty">Type a company name, NSE symbol, or BSE code…</li>;
    }
    if (searching) {
      return <li className="company-search-empty">Searching…</li>;
    }
    if (options.length === 0) {
      return <li className="company-search-empty">No match for “{query}”</li>;
    }
    return options.map((c) => (
      <li key={c.id}>
        <button
          type="button"
          className={`company-search-option${String(c.id) === value ? " selected" : ""}`}
          onClick={() => pick(c)}
        >
          <span className="company-search-name">{c.name}</span>
          <span className="company-search-codes">
            {c.nse_symbol && <span>{c.nse_symbol}</span>}
            {c.bse_scrip_code && <span>{c.bse_scrip_code}</span>}
          </span>
        </button>
      </li>
    ));
  }, [showHint, searching, options, query, value]);

  return (
    <div className="company-search" ref={rootRef}>
      <div className="company-search-input-wrap">
        <input
          className="company-search-input"
          type="text"
          value={displayValue}
          placeholder={selected ? companyLabel(selected) : placeholder}
          onChange={(e) => {
            setQuery(e.target.value);
            if (selected) {
              setSelected(undefined);
              onChange("");
            }
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
        />
        {(selected || query) && (
          <button type="button" className="company-search-clear" onClick={clear} aria-label="Clear">
            ×
          </button>
        )}
      </div>
      {open && (
        <ul className="company-search-list" role="listbox">
          {!value && !query && (
            <li>
              <button type="button" className="company-search-option" onClick={clear}>
                All companies
              </button>
            </li>
          )}
          {listContent}
        </ul>
      )}
    </div>
  );
}

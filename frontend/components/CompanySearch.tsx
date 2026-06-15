"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CompanyAdmin } from "@/lib/api";

function companyLabel(c: CompanyAdmin): string {
  const parts = [c.name];
  if (c.nse_symbol) parts.push(c.nse_symbol);
  parts.push(c.bse_scrip_code);
  return parts.join(" · ");
}

function matchesCompany(c: CompanyAdmin, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    c.name.toLowerCase().includes(q) ||
    c.bse_scrip_code.includes(q) ||
    (c.nse_symbol?.toLowerCase().includes(q) ?? false)
  );
}

export default function CompanySearch({
  companies,
  value,
  onChange,
  placeholder = "Search name or code…",
}: {
  companies: CompanyAdmin[];
  value: string;
  onChange: (companyId: string) => void;
  placeholder?: string;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const selected = useMemo(
    () => (value ? companies.find((c) => String(c.id) === value) : undefined),
    [companies, value],
  );

  const options = useMemo(() => {
    const filtered = companies.filter((c) => matchesCompany(c, query));
    return filtered.slice(0, 40);
  }, [companies, query]);

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
    onChange(String(c.id));
    setQuery("");
    setOpen(false);
  };

  const clear = () => {
    onChange("");
    setQuery("");
    setOpen(false);
  };

  const displayValue = open ? query : selected ? companyLabel(selected) : query;

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
            if (selected) onChange("");
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
          {options.length === 0 && (
            <li className="company-search-empty">No match for “{query}”</li>
          )}
          {options.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className={`company-search-option${String(c.id) === value ? " selected" : ""}`}
                onClick={() => pick(c)}
              >
                <span className="company-search-name">{c.name}</span>
                <span className="company-search-codes">
                  {c.nse_symbol && <span>{c.nse_symbol}</span>}
                  <span>{c.bse_scrip_code}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

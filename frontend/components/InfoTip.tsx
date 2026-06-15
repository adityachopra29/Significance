"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MetricHelp } from "@/lib/metricsHelp";

const GAP = 8;
const VIEWPORT_PAD = 12;

export default function InfoTip({ help }: { help: MetricHelp }) {
  const tipId = useId();
  const iconRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [style, setStyle] = useState<{ top: number; left: number }>({ top: 0, left: 0 });

  useEffect(() => setMounted(true), []);

  const updatePosition = useCallback(() => {
    const icon = iconRef.current;
    const popup = popupRef.current;
    if (!icon || !popup) return;

    const rect = icon.getBoundingClientRect();
    const popupWidth = popup.offsetWidth;
    const popupHeight = popup.offsetHeight;

    const spaceBelow = window.innerHeight - rect.bottom - GAP;
    const spaceAbove = rect.top - GAP;
    const showBelow = spaceBelow >= popupHeight || spaceBelow >= spaceAbove;

    let top = showBelow ? rect.bottom + GAP : rect.top - popupHeight - GAP;
    let left = rect.left + rect.width / 2 - popupWidth / 2;

    left = Math.max(VIEWPORT_PAD, Math.min(left, window.innerWidth - popupWidth - VIEWPORT_PAD));
    top = Math.max(VIEWPORT_PAD, Math.min(top, window.innerHeight - popupHeight - VIEWPORT_PAD));

    setStyle({ top, left });
  }, []);

  const show = () => setVisible(true);
  const hide = () => setVisible(false);

  useEffect(() => {
    if (!visible) return;
    const raf1 = requestAnimationFrame(() => {
      updatePosition();
      requestAnimationFrame(updatePosition);
    });
    window.addEventListener("scroll", updatePosition, true);
    window.addEventListener("resize", updatePosition);
    return () => {
      cancelAnimationFrame(raf1);
      window.removeEventListener("scroll", updatePosition, true);
      window.removeEventListener("resize", updatePosition);
    };
  }, [visible, updatePosition]);

  const popup =
    visible && mounted ? (
      <div
        ref={popupRef}
        id={tipId}
        className="info-tip-popup"
        role="tooltip"
        style={{ top: style.top, left: style.left }}
        onMouseEnter={show}
        onMouseLeave={hide}
      >
        <span className="info-tip-text">{help.description}</span>
        <span className="info-tip-scale">
          <strong>Scale:</strong> {help.scale}
        </span>
      </div>
    ) : null;

  return (
    <>
      <span className="info-tip">
        <button
          ref={iconRef}
          type="button"
          className="info-tip-icon"
          aria-label="What does this mean?"
          aria-describedby={visible ? tipId : undefined}
          onMouseEnter={show}
          onMouseLeave={hide}
          onFocus={show}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node)) hide();
          }}
        >
          i
        </button>
      </span>
      {mounted && popup ? createPortal(popup, document.body) : null}
    </>
  );
}

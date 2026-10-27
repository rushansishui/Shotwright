import type { ReactNode } from "react";

export function Segmented<T extends string>(props: {
  label: string;
  value: T;
  options: { value: T; label: string; disabled?: boolean; title?: string }[];
  onChange: (value: T) => void;
  disabled?: boolean;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={props.label}>
      {props.options.map((option) => (
        <button
          type="button"
          key={option.value}
          role="radio"
          aria-checked={props.value === option.value}
          className={props.value === option.value ? "on" : ""}
          disabled={props.disabled || option.disabled}
          title={option.title}
          onClick={() => props.onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Slider(props: {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (value: number) => string;
  onCommit: (value: number) => void;
  onInput?: (value: number) => void;
}) {
  return (
    <div className="field">
      <div className="field-row">
        <label htmlFor={props.id}>{props.label}</label>
        <span className="muted">{props.format(props.value)}</span>
      </div>
      <input
        id={props.id}
        type="range"
        min={props.min}
        max={props.max}
        step={props.step}
        value={props.value}
        onChange={(e) => props.onInput?.(Number(e.target.value))}
        onPointerUp={(e) => props.onCommit(Number((e.target as HTMLInputElement).value))}
        onKeyUp={(e) => props.onCommit(Number((e.target as HTMLInputElement).value))}
      />
    </div>
  );
}

export function Section(props: { step?: number; title: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <div className="card-head">
        {props.step !== undefined && <span className="step">{props.step}</span>}
        <h2>{props.title}</h2>
        {props.extra && <div className="card-extra">{props.extra}</div>}
      </div>
      {props.children}
    </section>
  );
}

export function Icon({ name }: { name: "up" | "down" | "trash" | "plus" | "lock" | "unlock" | "refresh" | "upload" | "merge" | "copy" | "search" | "film" }) {
  const paths: Record<string, ReactNode> = {
    up: <path d="M12 19V5M5 12l7-7 7 7" />,
    down: <path d="M12 5v14M19 12l-7 7-7-7" />,
    trash: <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />,
    plus: <path d="M12 5v14M5 12h14" />,
    lock: (
      <>
        <rect x="5" y="11" width="14" height="9" rx="2" />
        <path d="M8 11V8a4 4 0 0 1 8 0v3" />
      </>
    ),
    unlock: (
      <>
        <rect x="5" y="11" width="14" height="9" rx="2" />
        <path d="M8 11V8a4 4 0 0 1 7.5-2" />
      </>
    ),
    refresh: <path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" />,
    upload: <path d="M12 16V4M6 10l6-6 6 6M4 20h16" />,
    merge: <path d="M6 4v3a6 6 0 0 0 6 6M18 4v3a6 6 0 0 1-6 6v7M9 17l3 3 3-3" />,
    copy: (
      <>
        <rect x="9" y="9" width="11" height="11" rx="2" />
        <path d="M5 15V6a2 2 0 0 1 2-2h8" />
      </>
    ),
    search: (
      <>
        <circle cx="11" cy="11" r="6" />
        <path d="M20 20l-4.5-4.5" />
      </>
    ),
    film: (
      <>
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <path d="M10 9.5v5l4-2.5z" />
      </>
    ),
  };
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

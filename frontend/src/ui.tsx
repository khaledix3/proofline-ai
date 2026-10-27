import { tr } from "./i18n";
import type { ReactNode } from "react";
import type { Reference } from "./types";
export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    grid: (
      <>
        <rect x="3" y="3" width="6" height="6" rx="1" />
        <rect x="15" y="3" width="6" height="6" rx="1" />
        <rect x="3" y="15" width="6" height="6" rx="1" />
        <rect x="15" y="15" width="6" height="6" rx="1" />
      </>
    ),
    incident: (
      <>
        <path d="m12 3 10 18H2L12 3Z" />
        <path d="M12 9v5m0 3v.1" />
      </>
    ),
    replay: (
      <>
        <path d="M3 10a9 9 0 1 1 2 8M3 4v6h6" />
        <path d="m10 8 6 4-6 4V8Z" />
      </>
    ),
    refresh: (
      <>
        <path d="M20 7a9 9 0 0 0-15 0L2 10m0-6v6h6m-4 7a9 9 0 0 0 15 0l3-3m0 6v-6h-6" />
      </>
    ),
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    trace: (
      <>
        <circle cx="4" cy="6" r="2" />
        <circle cx="20" cy="18" r="2" />
        <path d="M6 6h5v12h7M11 12h9" />
      </>
    ),
    search: (
      <>
        <circle cx="10" cy="10" r="6" />
        <path d="m15 15 6 6" />
      </>
    ),
    shield: (
      <>
        <path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4Z" />
        <path d="m8 12 3 3 5-6" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] ?? paths.grid}
    </svg>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Notice({
  children,
  error = false,
}: {
  children: ReactNode;
  error?: boolean;
}) {
  return (
    <div
      className={`notice ${error ? "error" : ""}`}
      role={error ? "alert" : "status"}
    >
      {children}
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <Icon name="search" size={28} />
      <h3>{tr(title)}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Loading({
  text = "Loading recorded evidence…",
}: {
  text?: string;
}) {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      {tr(text)}
    </div>
  );
}
export function References({ items = [] }: { items?: Reference[] }) {
  return (
    <div className="refs">
      {items.map((ref, i) => (
        <details key={i}>
          <summary>
            {ref.table ?? ref.kind ?? "Evidence reference"}
            {typeof ref.key?.id === "number" ? ` #${ref.key.id}` : ""}
          </summary>
          <pre>{JSON.stringify(ref, null, 2)}</pre>
        </details>
      ))}
    </div>
  );
}

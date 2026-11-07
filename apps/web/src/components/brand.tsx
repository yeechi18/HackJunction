import Link from "next/link";

export function Brand() {
  return <Link className="brand" href="/" aria-label="出海工坊首页"><span className="brand-symbol" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="M6 23V9l10 7 10-7v14l-10-7-10 7Z" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round" /><path d="M16 6v20" stroke="currentColor" strokeWidth="2.2" /></svg></span><span className="brand-copy"><strong>出海工坊<span className="brand-period">.</span></strong><span>LISTING STUDIO</span></span></Link>;
}

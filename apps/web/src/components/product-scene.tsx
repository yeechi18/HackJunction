export function ProductScene({ compact = false }: { compact?: boolean }) {
  return <svg className={compact ? "product-scene compact" : "product-scene"} viewBox="0 0 600 460" role="img" aria-label="虚构桌面理线夹的结构示意，三个暖橙色理线夹整理着桌面线缆">
    <defs>
      <linearGradient id={compact ? "clip-small" : "clip-large"} x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stopColor="#f3a46b" /><stop offset="100%" stopColor="#b85932" /></linearGradient>
      <filter id={compact ? "shadow-small" : "shadow-large"} x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="13" /></filter>
    </defs>
    <g opacity=".12" stroke="#908a7b" strokeWidth="1"><path d="M0 150h600M0 210h600M0 270h600M0 330h600M0 390h600M90 80v380M210 80v380M330 80v380M450 80v380M570 80v380" /></g>
    <path d="M-30 300c90 0 96-88 172-88s53 104 123 104 37-122 104-122 78 55 135 55 77-61 110-61" fill="none" stroke="#d6d0c2" strokeWidth="12" />
    <path d="M-30 296c90 0 96-88 172-88s53 104 123 104 37-122 104-122 78 55 135 55 77-61 110-61" fill="none" stroke="#f8f6ed" strokeWidth="9" />
    <ellipse cx="301" cy="345" rx="188" ry="28" fill="#3e2b16" opacity=".14" filter={`url(#${compact ? "shadow-small" : "shadow-large"})`} />
    <g transform="translate(95 230) rotate(-16)"><rect x="0" y="0" width="105" height="79" rx="22" fill="#8f482e" /><rect x="0" y="-15" width="105" height="79" rx="22" fill={`url(#${compact ? "clip-small" : "clip-large"})`} /><rect x="36" y="-15" width="32" height="79" rx="16" fill="#863c26" /><path d="M43-11h18v66H43z" fill="#a95632" /><path d="M52-90v215" stroke="#e9e2d5" strokeWidth="13" strokeLinecap="round" /><path d="M49-90v215" stroke="#fffdf5" strokeWidth="5" strokeLinecap="round" /></g>
    <g transform="translate(225 185) rotate(-16)"><rect x="0" y="0" width="115" height="85" rx="23" fill="#8f482e" /><rect x="0" y="-16" width="115" height="85" rx="23" fill={`url(#${compact ? "clip-small" : "clip-large"})`} /><rect x="40" y="-16" width="34" height="85" rx="17" fill="#863c26" /><path d="M57-120v235" stroke="#272d29" strokeWidth="15" strokeLinecap="round" /><path d="M53-120v235" stroke="#515750" strokeWidth="4" strokeLinecap="round" /></g>
    <g transform="translate(375 234) rotate(-16)"><rect x="0" y="0" width="105" height="79" rx="22" fill="#8f482e" /><rect x="0" y="-15" width="105" height="79" rx="22" fill={`url(#${compact ? "clip-small" : "clip-large"})`} /><rect x="36" y="-15" width="32" height="79" rx="16" fill="#863c26" /><path d="M52-85v204" stroke="#b7bda5" strokeWidth="12" strokeLinecap="round" /><path d="M49-85v204" stroke="#dce0cd" strokeWidth="4" strokeLinecap="round" /></g>
    <g fill="none" stroke="#6b7061" strokeWidth="1.2"><path d="M410 124h70v-33M155 320l-35 36H65" /><circle cx="410" cy="124" r="3" fill="#6b7061" /><circle cx="155" cy="320" r="3" fill="#6b7061" /></g>
    <text x="444" y="77" fontSize="11" fill="#6b7061" letterSpacing="2">LESS CLUTTER.</text><text x="39" y="380" fontSize="11" fill="#6b7061" letterSpacing="2">MORE SPACE.</text>
  </svg>;
}

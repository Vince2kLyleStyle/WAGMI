import { Html, Head, Main, NextScript } from 'next/document';

export default function Document() {
  return (
    <Html lang="en">
      <Head>
        {/* Inter + JetBrains Mono from Google Fonts */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
        {/* Favicon — green accent */}
        <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='%23050508'/><rect width='32' height='32' rx='6' fill='none' stroke='%2300cc88' stroke-width='2'/><text x='50%25' y='55%25' dominant-baseline='middle' text-anchor='middle' fill='%2300cc88' font-size='12' font-family='system-ui' font-weight='bold'>W</text></svg>" />
        {/* Primary meta */}
        <meta name="description" content="WAGMI — AI-powered perpetual trading bot on Hyperliquid. Multi-strategy signals filtered through a 9-agent AI brain." />
        {/* Open Graph */}
        <meta property="og:title" content="WAGMI — AI-Powered Perpetual Trading" />
        <meta property="og:description" content="AI-powered perpetual trading on Hyperliquid. Multi-strategy signals, 9-agent AI brain, real-time performance." />
        <meta property="og:type" content="website" />
        <meta property="og:site_name" content="WAGMI" />
        <meta property="og:url" content="https://wagmi.trading" />
        {/* Twitter Card */}
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:title" content="WAGMI — AI-Powered Perpetual Trading" />
        <meta name="twitter:description" content="AI-powered perpetual trading on Hyperliquid. Multi-strategy signals, 9-agent AI brain." />
        {/* Theme */}
        <meta name="theme-color" content="#050508" />
        {/* Global styles */}
        <style>{`
          *, *::before, *::after { box-sizing: border-box; }
          html {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            scroll-behavior: smooth;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
          }
          body {
            margin: 0;
            padding: 0;
            background: #050508;
            color: #f0f0f5;
          }
          a { color: inherit; text-decoration: none; }
          button { font-family: inherit; cursor: pointer; }
          code, pre, .mono {
            font-family: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;
          }

          /* ── Mobile responsiveness ──────────────────────────
             The dashboard is built almost entirely with inline styles, so global
             classes can't reach it — we override inline grid/width via attribute
             selectors + !important. On phones every multi-column grid becomes a
             single stacked column (the site is card-based, so stacking is the
             natural mobile layout), and nothing is allowed to widen past the
             viewport. */
          @media (max-width: 768px) {
            /* Collapse ALL inline grids (1fr 1fr, repeat(n,1fr), minmax(0,Xfr)…,
               auto-fit/auto-fill) to one column. Icon+text "auto 1fr" rows stack,
               which is acceptable on a phone. */
            [style*="grid-template-columns"] { grid-template-columns: 1fr !important; }
            /* Critical: let grid/flex CHILDREN shrink below their content size. Without
               this, one wide child (a data table or diagram) balloons its 1fr track past
               the viewport and every sibling grid then renders multi-column and bleeds. */
            [style*="display:grid"] > *, [style*="display: grid"] > *,
            [style*="display:flex"] > *, [style*="display: flex"] > * { min-width: 0 !important; }
            [style*="minWidth"], [style*="min-width"] { min-width: 0 !important; }
            /* Media + diagrams never exceed the viewport. */
            img, video, canvas, svg { max-width: 100% !important; height: auto; }
            table, pre { display: block; overflow-x: auto; -webkit-overflow-scrolling: touch; }
          }

          /* ── CSS custom properties ──────────────────────── */
          :root {
            /* Brand */
            --color-brand: #00cc88;
            --color-brand-dark: #00a86b;
            --color-brand-glow: rgba(0,204,136,0.15);

            /* Semantic */
            --color-bull: #00cc88;
            --color-bear: #ff4466;
            --color-warn: #ffaa00;
            --color-info: #4488ff;

            /* Dark surfaces */
            --color-bg: #050508;
            --color-surface: #0a0a0f;
            --color-surface-hover: #0f0f18;
            --color-card: #0d0d14;
            --color-border: rgba(255,255,255,0.06);
            --color-border-bright: rgba(255,255,255,0.12);

            /* Text */
            --color-text: #f0f0f5;
            --color-text-sub: #a0a0b8;
            --color-muted: #6b6b7b;
            --color-faint: #333344;

            /* Radii */
            --radius-xs: 4px;
            --radius-sm: 6px;
            --radius-md: 10px;
            --radius-lg: 12px;
            --radius-xl: 16px;
            --radius-pill: 9999px;
          }

          /* Skip-to-content */
          .skip-to-content {
            position: absolute;
            top: -100%;
            left: 12px;
            z-index: 9999;
            padding: 8px 16px;
            background: var(--color-brand);
            color: #050508;
            font-size: 14px;
            font-weight: 700;
            border-radius: 0 0 var(--radius-sm) var(--radius-sm);
            text-decoration: none;
            transition: top 0.15s ease;
          }
          .skip-to-content:focus { top: 0; outline: none; }

          /* Focus ring */
          :focus-visible {
            outline: none;
            box-shadow: 0 0 0 2px rgba(0,204,136,0.5), 0 0 12px rgba(0,204,136,0.15);
          }
          :focus:not(:focus-visible) { outline: none; }

          /* Skeleton shimmer */
          @keyframes shimmer {
            0% { background-position: -200% 0; }
            100% { background-position: 200% 0; }
          }
          .skeleton {
            background: linear-gradient(
              90deg,
              #0d0d14 25%,
              #141422 50%,
              #0d0d14 75%
            );
            background-size: 200% 100%;
            border-radius: var(--radius-sm);
            animation: shimmer 1.5s ease-in-out infinite;
          }

          /* Live dot pulse */
          @keyframes livePulse {
            0%, 100% { box-shadow: 0 0 0 0 rgba(0,204,136,0.5); opacity: 1; }
            50%       { box-shadow: 0 0 0 5px rgba(0,204,136,0); opacity: 0.8; }
          }
          .live-dot { animation: livePulse 2s ease-in-out infinite; }

          /* Fade in up */
          @keyframes fadeInUp {
            from { opacity: 0; transform: translateY(14px); }
            to   { opacity: 1; transform: translateY(0); }
          }
          .fade-in   { animation: fadeInUp 0.3s cubic-bezier(.22,.68,0,1.2) both; }
          .fade-in-1 { animation: fadeInUp 0.3s cubic-bezier(.22,.68,0,1.2) 0.05s both; }
          .fade-in-2 { animation: fadeInUp 0.3s cubic-bezier(.22,.68,0,1.2) 0.10s both; }
          .fade-in-3 { animation: fadeInUp 0.3s cubic-bezier(.22,.68,0,1.2) 0.15s both; }
          .fade-in-4 { animation: fadeInUp 0.3s cubic-bezier(.22,.68,0,1.2) 0.20s both; }

          /* Gradient text */
          @keyframes gradientShift {
            0%   { background-position: 0% 50%; }
            50%  { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
          }
          .gradient-text {
            background: linear-gradient(135deg, #00cc88, #00e699, #4488ff);
            background-size: 200% 200%;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            animation: gradientShift 4s ease infinite;
          }

          /* Stagger reveal */
          .stagger-reveal > * {
            opacity: 0;
            transform: translateY(10px);
            animation: fadeInUp 0.3s cubic-bezier(.22,.68,0,1.2) both;
          }
          .stagger-reveal > *:nth-child(1) { animation-delay: 0.05s; }
          .stagger-reveal > *:nth-child(2) { animation-delay: 0.10s; }
          .stagger-reveal > *:nth-child(3) { animation-delay: 0.15s; }
          .stagger-reveal > *:nth-child(4) { animation-delay: 0.20s; }
          .stagger-reveal > *:nth-child(5) { animation-delay: 0.25s; }
          .stagger-reveal > *:nth-child(6) { animation-delay: 0.30s; }
          .stagger-reveal > *:nth-child(n+7) { animation-delay: 0.35s; }

          /* Card hover — subtle lift only */
          .card-hover {
            transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
          }
          .card-hover:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 32px rgba(0,0,0,0.5);
            border-color: rgba(255,255,255,0.10) !important;
          }

          /* PnL glow effects */
          .glow-bull { box-shadow: 0 0 16px rgba(0,204,136,0.15), 0 4px 12px rgba(0,0,0,0.3); }
          .glow-bear { box-shadow: 0 0 16px rgba(255,68,102,0.15), 0 4px 12px rgba(0,0,0,0.3); }
          .glow-brand { box-shadow: 0 0 20px rgba(0,204,136,0.15), 0 4px 12px rgba(0,0,0,0.3); }

          /* Pill badge */
          .badge {
            display: inline-flex; align-items: center; gap: 4px;
            padding: 2px 8px; border-radius: 9999px;
            font-size: 11px; font-weight: 600; letter-spacing: 0.3px;
          }
          .badge-bull  { background: rgba(0,204,136,0.12);  color: #00cc88; }
          .badge-bear  { background: rgba(255,68,102,0.12);  color: #ff4466; }
          .badge-warn  { background: rgba(255,170,0,0.12);   color: #ffaa00; }
          .badge-info  { background: rgba(68,136,255,0.12);  color: #4488ff; }
          .badge-muted { background: rgba(107,107,123,0.12); color: #a0a0b8; }

          /* Section label */
          .section-label {
            font-size: 10px; font-weight: 700; letter-spacing: 1.4px;
            text-transform: uppercase; color: var(--color-muted);
            display: flex; align-items: center; gap: 8px;
          }
          .section-label::after {
            content: ''; flex: 1; height: 1px;
            background: var(--color-border);
          }

          /* Scrollbar */
          ::-webkit-scrollbar { width: 4px; height: 4px; }
          ::-webkit-scrollbar-track { background: #050508; }
          ::-webkit-scrollbar-thumb {
            background: rgba(255,255,255,0.1);
            border-radius: 10px;
          }
          ::-webkit-scrollbar-thumb:hover {
            background: rgba(0,204,136,0.3);
          }

          /* Table base */
          table { border-collapse: collapse; width: 100%; }
          th { text-align: left; }

          /* Number font */
          .num { font-family: 'JetBrains Mono', monospace; font-variant-numeric: tabular-nums; }

          /* Text selection */
          ::selection {
            background: rgba(0,204,136,0.25);
            color: #f0f0f5;
          }

          /* Reduced motion */
          @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
              animation-duration: 0.01ms !important;
              animation-iteration-count: 1 !important;
              transition-duration: 0.01ms !important;
            }
          }
        `}</style>
      </Head>
      <body>
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}

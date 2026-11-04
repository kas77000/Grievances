"""Theme tokens + CSS injection + shared render helpers.

Runtime light/dark switching: we inject a full stylesheet with concrete token values for the
active theme on every rerun (robust across Streamlit versions). Tokens follow DESIGN.md.
Spacing uses a 4px-based scale so section/component rhythm is consistent.
"""
from __future__ import annotations

import html as _html

import streamlit as st

TOKENS = {
    "dark": {
        "bg": "#0d1016", "surface": "#161b24", "surface2": "#1b212c", "border": "#28303c",
        "border_soft": "#20262f",
        "text": "#e7ebf1", "muted": "#98a2b2", "faint": "#6b7686",
        "accent": "#6a86fb", "accent_weak": "#1e2740", "accent_border": "#33406b",
        "up": "#3fb26a", "down": "#e5564a", "warn": "#e0a530", "halt": "#b07de0",
        "shadow": "0 1px 2px rgba(0,0,0,.35)",
    },
    "light": {
        "bg": "#f5f6f9", "surface": "#ffffff", "surface2": "#eef1f6", "border": "#e2e6ee",
        "border_soft": "#eaeef4",
        "text": "#141922", "muted": "#5c6675", "faint": "#8a93a2",
        "accent": "#3b5bdb", "accent_weak": "#eef2fe", "accent_border": "#c8d3f7",
        "up": "#12703a", "down": "#bf2d20", "warn": "#8a5b00", "halt": "#6f26a0",
        "shadow": "0 1px 2px rgba(20,30,50,.05)",
    },
}

SEV_TONE = {"critical": "halt", "high": "down", "warn": "warn", "info": "muted"}


def current_theme() -> str:
    return st.session_state.get("theme", "dark")


def toggle_theme():
    """Flip theme. Native widgets (dataframe/charts) only re-theme on a fresh session, so we
    persist the choice in the URL and hard-reload the page."""
    new = "light" if current_theme() == "dark" else "dark"
    st.session_state["theme"] = new
    st.query_params["theme"] = new
    _apply_native_theme(new)
    import streamlit.components.v1 as components
    components.html("<script>window.parent.location.reload()</script>", height=0)
    st.stop()


def _apply_native_theme(theme: str):
    """Make Streamlit's own widgets (dataframe, charts, inputs) follow our theme."""
    t = TOKENS[theme]
    try:
        st._config.set_option("theme.base", "dark" if theme == "dark" else "light")
        st._config.set_option("theme.backgroundColor", t["bg"])
        st._config.set_option("theme.secondaryBackgroundColor", t["surface2"])
        st._config.set_option("theme.textColor", t["text"])
        st._config.set_option("theme.primaryColor", t["accent"])
        st._config.set_option("theme.borderColor", t["border"])
    except Exception:
        pass


def inject(theme: str | None = None):
    theme = theme or current_theme()
    _apply_native_theme(theme)
    t = TOKENS[theme]
    css = f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
      :root {{
        --bg:{t['bg']}; --surface:{t['surface']}; --surface2:{t['surface2']};
        --border:{t['border']}; --border-soft:{t['border_soft']};
        --text:{t['text']}; --muted:{t['muted']}; --faint:{t['faint']};
        --accent:{t['accent']}; --accent-weak:{t['accent_weak']}; --accent-border:{t['accent_border']};
        --up:{t['up']}; --down:{t['down']}; --warn:{t['warn']}; --halt:{t['halt']};
        --shadow:{t['shadow']};
        --s1:.25rem; --s2:.5rem; --s3:.75rem; --s4:1rem; --s5:1.5rem; --s6:2rem;
        --radius:12px; --radius-sm:8px;
      }}
      html, body, .stApp, [data-testid="stAppViewContainer"] {{
        background: var(--bg); color: var(--text);
        font-family: 'Inter', -apple-system, "Segoe UI", system-ui, sans-serif;
      }}
      [data-testid="stHeader"] {{ background: transparent; height: 0; }}
      .block-container {{ padding-top: 2rem; padding-bottom: 3rem; max-width: 1480px; }}
      /* consistent vertical rhythm between stacked elements */
      [data-testid="stVerticalBlock"] {{ gap: .85rem; }}
      [data-testid="stHorizontalBlock"] {{ gap: .75rem; }}

      /* ---- sidebar ---- */
      [data-testid="stSidebar"], [data-testid="stSidebarContent"] {{
        background: var(--surface2); border-right: 1px solid var(--border);
      }}
      [data-testid="stSidebar"] .block-container {{ padding-top: 1.4rem; }}
      [data-testid="stSidebar"] .stButton>button {{
        justify-content:flex-start; font-weight:500; border-radius:var(--radius-sm);
        border:1px solid transparent; background:transparent; color:var(--text);
        padding:.45rem .7rem; transition:background .15s ease;
      }}
      [data-testid="stSidebar"] .stButton>button:hover {{ background:var(--surface); }}
      [data-testid="stSidebar"] .stButton>button[kind="primary"] {{
        background:var(--accent-weak); color:var(--accent); font-weight:600;
        border-color:var(--accent-border);
      }}

      /* ---- headings ---- */
      h1,h2,h3,h4 {{ color: var(--text); font-weight: 600; letter-spacing: -.012em; }}
      h2 {{ font-size: 1.55rem; margin: .1rem 0 .6rem; }}
      [data-testid="stMarkdownContainer"] p {{ line-height: 1.5; }}

      /* tabular numerics everywhere data lives */
      [data-testid="stDataFrame"], .stMetric, .amx-tab, .amx-mono, .amx-tile .val {{
        font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1;
      }}
      .amx-eyebrow {{ color: var(--faint); font-size: 11.5px; font-weight: 600;
        letter-spacing: .08em; text-transform: uppercase; margin: 0 0 .3rem; }}

      /* ---- toolbar strip (source / date) ---- */
      .amx-strip {{ display:flex; align-items:center; gap:.6rem; flex-wrap:wrap;
        padding:.15rem 0 .1rem; }}

      /* ---- metric tiles ---- */
      .amx-tiles {{ display:flex; gap:.6rem; flex-wrap:wrap; margin:.15rem 0 .2rem; }}
      .amx-tile {{ background:var(--surface); border:1px solid var(--border);
        border-radius:var(--radius); padding:.7rem .95rem; min-width:140px; flex:1 1 140px;
        box-shadow:var(--shadow); }}
      .amx-tile .lbl {{ color:var(--muted); font-size:11px; font-weight:600;
        text-transform:uppercase; letter-spacing:.05em; }}
      .amx-tile .val {{ font-size:21px; font-weight:600; margin-top:.2rem; line-height:1.1; }}
      .amx-tile .sub {{ color:var(--faint); font-size:11.5px; margin-top:.25rem; }}
      .val.up {{ color:var(--up); }} .val.down {{ color:var(--down); }}

      /* ---- badges ---- */
      .amx-badge {{ display:inline-flex; align-items:center; gap:.3rem; padding:.14rem .55rem;
        border-radius:999px; font-size:11px; font-weight:600; border:1px solid transparent;
        line-height:1.35; white-space:nowrap; }}
      .amx-badge.info {{ color:var(--muted); background:var(--surface2); border-color:var(--border); }}
      .amx-badge.warn {{ color:var(--warn); background:color-mix(in srgb,var(--warn) 13%,transparent);
        border-color:color-mix(in srgb,var(--warn) 34%,transparent); }}
      .amx-badge.down,.amx-badge.high {{ color:var(--down);
        background:color-mix(in srgb,var(--down) 13%,transparent);
        border-color:color-mix(in srgb,var(--down) 34%,transparent); }}
      .amx-badge.halt,.amx-badge.critical {{ color:var(--halt);
        background:color-mix(in srgb,var(--halt) 14%,transparent);
        border-color:color-mix(in srgb,var(--halt) 36%,transparent); }}
      .amx-badge.accent {{ color:var(--accent);
        background:var(--accent-weak); border-color:var(--accent-border); }}
      .amx-badge.up {{ color:var(--up); background:color-mix(in srgb,var(--up) 13%,transparent);
        border-color:color-mix(in srgb,var(--up) 34%,transparent); }}

      /* ---- situation rows ---- */
      .amx-sit {{ display:flex; gap:.7rem; align-items:flex-start; padding:.7rem .9rem;
        border:1px solid var(--border); border-radius:var(--radius); background:var(--surface);
        margin-bottom:.5rem; box-shadow:var(--shadow); }}
      .amx-sit .body {{ flex:1; }}
      .amx-sit .name {{ font-weight:600; font-size:13.5px; }}
      .amx-sit .detail {{ color:var(--muted); font-size:12.5px; margin-top:.2rem; line-height:1.45; }}
      .amx-sit .meta {{ color:var(--faint); font-size:11px; margin-top:.3rem;
        font-variant-numeric:tabular-nums; }}

      /* ---- panels & empty state ---- */
      .amx-panel {{ background:var(--surface); border:1px solid var(--border);
        border-radius:var(--radius); padding:1rem 1.15rem; box-shadow:var(--shadow); }}
      .amx-empty {{ display:flex; flex-direction:column; align-items:center; justify-content:center;
        text-align:center; gap:.5rem; padding:3.2rem 1rem; border:1px dashed var(--border);
        border-radius:var(--radius); background:var(--surface); color:var(--muted); }}
      .amx-empty .t {{ color:var(--text); font-weight:600; font-size:15px; }}
      .amx-empty .d {{ font-size:12.5px; max-width:420px; line-height:1.5; }}
      .amx-empty svg {{ opacity:.7; }}

      /* ---- streamlit widget refinements ---- */
      [data-testid="stExpander"] {{ border:1px solid var(--border); border-radius:var(--radius);
        background:var(--surface); box-shadow:var(--shadow); }}
      [data-testid="stExpander"] summary {{ font-weight:500; }}
      [data-testid="stMain"] .stButton>button[kind="primary"] {{
        background:var(--accent); border:none; font-weight:600; border-radius:var(--radius-sm); }}
      [data-testid="stMain"] .stButton>button {{ border-radius:var(--radius-sm); }}
      [data-testid="stDataFrame"] {{ border-radius:var(--radius); }}
      .stTabs [data-baseweb="tab-list"] {{ gap:.15rem; border-bottom:1px solid var(--border); }}
      .stTabs [data-baseweb="tab"] {{ font-size:13px; padding:.35rem .75rem; }}
      .stTabs [data-baseweb="tab-panel"] {{ padding-top:1rem; }}
      hr {{ margin:1rem 0; border-color:var(--border-soft); }}
      a, .stMarkdown a {{ color:var(--accent); }}
      label, [data-testid="stWidgetLabel"] p {{ font-size:12px !important; color:var(--muted) !important;
        font-weight:500 !important; }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


# ---- render helpers -------------------------------------------------------
def esc(s) -> str:
    return _html.escape(str(s))


def badge(text: str, kind: str = "info") -> str:
    return f'<span class="amx-badge {kind}">{esc(text)}</span>'


def tile(label: str, value: str, sub: str = "", tone: str = "") -> str:
    cls = f"val {tone}".strip()
    sub_html = f'<div class="sub">{esc(sub)}</div>' if sub else ""
    return (f'<div class="amx-tile"><div class="lbl">{esc(label)}</div>'
            f'<div class="{cls}">{esc(value)}</div>{sub_html}</div>')


def tiles(items: list[str]) -> str:
    return '<div class="amx-tiles">' + "".join(items) + "</div>"


def signed_pct(pct: float) -> tuple[str, str]:
    if pct is None:
        return "—", ""
    arrow = "▲" if pct >= 0 else "▼"
    tone = "up" if pct >= 0 else "down"
    return f"{arrow} {pct:+.2f}%", tone


def eyebrow(text: str):
    st.markdown(f'<div class="amx-eyebrow">{esc(text)}</div>', unsafe_allow_html=True)


def empty_state(title: str, detail: str = "", icon: str = "search"):
    icons = {
        "search": ('<svg width="34" height="34" viewBox="0 0 24 24" fill="none" '
                   'stroke="currentColor" stroke-width="1.6"><circle cx="11" cy="11" r="7"/>'
                   '<path d="m21 21-4.3-4.3"/></svg>'),
    }
    st.markdown(
        f'<div class="amx-empty">{icons.get(icon, "")}'
        f'<div class="t">{esc(title)}</div>'
        f'<div class="d">{esc(detail)}</div></div>', unsafe_allow_html=True)


def vspace(rem: float = 0.75):
    st.markdown(f"<div style='height:{rem}rem'></div>", unsafe_allow_html=True)

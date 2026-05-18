from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, HTMLResponse

from backend.app.core.admin_auth import AdminPrincipal, require_admin_auth

router = APIRouter(prefix="/admin", tags=["admin-ui"])
AdminDep = Annotated[AdminPrincipal, Depends(require_admin_auth)]


PAGES = {
    "overview": ("Overview", "/api/v1/admin/overview"),
    "users": ("Users", "/api/v1/admin/users"),
    "jobs": ("Jobs", "/api/v1/admin/jobs"),
    "payments": ("Payments", "/api/v1/admin/payments"),
    "runpod": ("RunPod", "/api/v1/admin/runpod/pods"),
    "business": ("Business Accounts", "/api/v1/admin/business-accounts"),
    "audit": ("Audit Logs", "/api/v1/admin/audit-logs"),
}
ADMIN_CSS_PATH = Path(__file__).resolve().parents[1] / "static" / "admin.css"


@router.get("/static/admin.css", response_class=FileResponse)
async def admin_css(_: AdminDep) -> FileResponse:
    return FileResponse(ADMIN_CSS_PATH, media_type="text/css")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_index(_: AdminDep) -> HTMLResponse:
    return _admin_page("overview")


@router.get("/users", response_class=HTMLResponse)
async def admin_users(_: AdminDep) -> HTMLResponse:
    return _admin_page("users")


@router.get("/jobs", response_class=HTMLResponse)
async def admin_jobs(_: AdminDep) -> HTMLResponse:
    return _admin_page("jobs")


@router.get("/payments", response_class=HTMLResponse)
async def admin_payments(_: AdminDep) -> HTMLResponse:
    return _admin_page("payments")


@router.get("/runpod", response_class=HTMLResponse)
async def admin_runpod(_: AdminDep) -> HTMLResponse:
    return _admin_page("runpod")


@router.get("/business", response_class=HTMLResponse)
async def admin_business(_: AdminDep) -> HTMLResponse:
    return _admin_page("business")


@router.get("/audit", response_class=HTMLResponse)
async def admin_audit(_: AdminDep) -> HTMLResponse:
    return _admin_page("audit")


def _admin_page(page: str) -> HTMLResponse:
    title, endpoint = PAGES[page]
    nav = "\n".join(
        f'<a class="{"active" if key == page else ""}" href="{href}">{label}</a>'
        for key, (label, _endpoint) in PAGES.items()
        for href in ["/admin" if key == "overview" else f"/admin/{key}"]
    )
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Admin - {title}</title>
  <link rel="stylesheet" href="/admin/static/admin.css">
</head>
<body>
  <aside>
    <h1>Operator</h1>
    <nav>{nav}</nav>
  </aside>
  <main>
    <header>
      <div>
        <p class="eyebrow">Read-only admin panel</p>
        <h2>{title}</h2>
      </div>
      <button id="refresh" type="button">Refresh</button>
    </header>
    <section id="content" class="panel">Loading...</section>
  </main>
  <script>
    const endpoint = {endpoint!r};
    const content = document.getElementById("content");
    const refresh = document.getElementById("refresh");

    function escapeHtml(value) {{
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }}

    function flatten(value, prefix = "") {{
      if (value === null || typeof value !== "object" || Array.isArray(value)) {{
        return [[prefix || "value", value]];
      }}
      return Object.entries(value).flatMap(([key, nested]) => {{
        const label = prefix ? `${{prefix}}.${{key}}` : key;
        if (nested !== null && typeof nested === "object" && !Array.isArray(nested)) {{
          return flatten(nested, label);
        }}
        return [[label, nested]];
      }});
    }}

    function renderTable(items) {{
      if (!items.length) return '<p class="muted">No records.</p>';
      const keys = Array.from(new Set(items.flatMap(item => Object.keys(item))));
      const head = keys.map(key => `<th>${{escapeHtml(key)}}</th>`).join("");
      const rows = items.map(item => {{
        const cells = keys.map(key => `<td>${{escapeHtml(formatValue(item[key]))}}</td>`).join("");
        return `<tr>${{cells}}</tr>`;
      }}).join("");
      return `<div class="table-wrap"><table><thead><tr>${{head}}</tr></thead>`
        + `<tbody>${{rows}}</tbody></table></div>`;
    }}

    function formatValue(value) {{
      if (value === null || value === undefined) return "";
      if (Array.isArray(value)) return `${{value.length}} item(s)`;
      if (typeof value === "object") return JSON.stringify(value);
      return value;
    }}

    function render(data) {{
      if (Array.isArray(data.items)) return renderTable(data.items);
      if (Array.isArray(data.pods)) return renderTable(data.pods);
      const rows = flatten(data).map(([key, value]) =>
        `<tr><th>${{escapeHtml(key)}}</th><td>${{escapeHtml(formatValue(value))}}</td></tr>`
      ).join("");
      return `<table class="kv"><tbody>${{rows}}</tbody></table>`;
    }}

    async function load() {{
      content.innerHTML = '<p class="muted">Loading...</p>';
      try {{
        const response = await fetch(endpoint, {{ credentials: "same-origin" }});
        if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
        const data = await response.json();
        content.innerHTML = render(data);
      }} catch (error) {{
        content.innerHTML = `<p class="error">Failed to load: ${{escapeHtml(error.message)}}</p>`;
      }}
    }}

    refresh.addEventListener("click", load);
    load();
  </script>
</body>
</html>"""
    )

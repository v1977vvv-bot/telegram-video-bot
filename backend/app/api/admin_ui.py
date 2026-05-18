from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, HTMLResponse

from backend.app.core.admin_auth import AdminPrincipal, require_admin_auth
from shared.app.config import get_settings

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
    actions_enabled = "true" if get_settings().admin_actions_enabled else "false"
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
        <p class="eyebrow">Admin operator panel</p>
        <h2>{title}</h2>
      </div>
      <button id="refresh" type="button">Refresh</button>
    </header>
    <section id="actions" class="panel actions"></section>
    <p id="action-status" class="action-status"></p>
    <section id="content" class="panel">Loading...</section>
  </main>
  <script>
    const page = {page!r};
    const endpoint = {endpoint!r};
    const actionsEnabled = {actions_enabled};
    const content = document.getElementById("content");
    const actions = document.getElementById("actions");
    const actionStatus = document.getElementById("action-status");
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

    function renderActions() {{
      if (!actionsEnabled) {{
        actions.innerHTML = '<p class="muted">Admin actions are disabled.</p>';
        return;
      }}
      const forms = {{
        overview: `
          <h3>Queue controls</h3>
          <form class="action-form" data-action="retry_waiting">
            <input name="reason" placeholder="Reason" required>
            <button type="submit">Retry waiting jobs</button>
          </form>`,
        users: `
          <h3>User controls</h3>
          <form class="action-form" data-action="personal_topup">
            <input name="user_id" placeholder="User UUID" required>
            <input name="amount_usd" placeholder="Amount USD" required>
            <input name="reason" placeholder="Reason" required>
            <button type="submit">Top up personal balance</button>
          </form>
          <form class="action-form" data-action="user_block">
            <input name="user_id" placeholder="User UUID" required>
            <input name="reason" placeholder="Reason" required>
            <button type="submit">Block user</button>
          </form>
          <form class="action-form" data-action="user_unblock">
            <input name="user_id" placeholder="User UUID" required>
            <input name="reason" placeholder="Reason" required>
            <button type="submit">Unblock user</button>
          </form>`,
        jobs: `
          <h3>Job controls</h3>
          <form class="action-form" data-action="fail_refund">
            <input name="job_id" placeholder="Job UUID" required>
            <input name="reason" placeholder="Reason" required>
            <button type="submit">Fail and refund job</button>
          </form>`,
        runpod: `
          <h3>RunPod controls</h3>
          <form class="action-form" data-action="terminate_pod">
            <input name="runpod_pod_id" placeholder="RunPod pod id" required>
            <input name="reason" placeholder="Reason" required>
            <button type="submit">Terminate pod</button>
          </form>`,
        business: `
          <h3>Business controls</h3>
          <form class="action-form" data-action="business_topup">
            <input name="business_account_id" placeholder="Business account UUID" required>
            <input name="amount_usd" placeholder="Amount USD" required>
            <input name="reason" placeholder="Reason" required>
            <button type="submit">Top up business balance</button>
          </form>
          <form class="action-form" data-action="business_add_member">
            <input name="business_account_id" placeholder="Business account UUID" required>
            <input name="telegram_id" placeholder="Telegram ID">
            <input name="user_id" placeholder="User UUID">
            <select name="role">
              <option value="member">member</option>
              <option value="owner">owner</option>
            </select>
            <input name="reason" placeholder="Reason" required>
            <button type="submit">Add member</button>
          </form>
          <form class="action-form" data-action="business_remove_member">
            <input name="business_account_id" placeholder="Business account UUID" required>
            <input name="user_id" placeholder="User UUID" required>
            <input name="reason" placeholder="Reason" required>
            <button type="submit">Deactivate member</button>
          </form>`,
      }};
      actions.innerHTML = forms[page] || '<p class="muted">No actions for this page.</p>';
    }}

    function formObject(form) {{
      const data = Object.fromEntries(new FormData(form).entries());
      Object.keys(data).forEach(key => {{
        if (data[key] === "") delete data[key];
      }});
      return data;
    }}

    async function submitAction(event) {{
      event.preventDefault();
      const form = event.target;
      const data = formObject(form);
      let path = "";
      let body = {{ reason: data.reason }};
      if (form.dataset.action === "retry_waiting") {{
        path = "/api/v1/admin/jobs/retry-waiting";
      }} else if (form.dataset.action === "personal_topup") {{
        path = `/api/v1/admin/users/${{data.user_id}}/balance/top-up`;
        body.amount_usd = data.amount_usd;
      }} else if (form.dataset.action === "user_block") {{
        path = `/api/v1/admin/users/${{data.user_id}}/block`;
      }} else if (form.dataset.action === "user_unblock") {{
        path = `/api/v1/admin/users/${{data.user_id}}/unblock`;
      }} else if (form.dataset.action === "fail_refund") {{
        path = `/api/v1/admin/jobs/${{data.job_id}}/fail-refund`;
      }} else if (form.dataset.action === "terminate_pod") {{
        path = `/api/v1/admin/runpod/pods/${{data.runpod_pod_id}}/terminate`;
      }} else if (form.dataset.action === "business_topup") {{
        path = `/api/v1/admin/business-accounts/${{data.business_account_id}}/balance/top-up`;
        body.amount_usd = data.amount_usd;
      }} else if (form.dataset.action === "business_add_member") {{
        path = `/api/v1/admin/business-accounts/${{data.business_account_id}}/members`;
        body.telegram_id = data.telegram_id ? Number(data.telegram_id) : undefined;
        body.user_id = data.user_id;
        body.role = data.role || "member";
      }} else if (form.dataset.action === "business_remove_member") {{
        path = `/api/v1/admin/business-accounts/${{data.business_account_id}}`
          + `/members/${{data.user_id}}/deactivate`;
      }}
      actionStatus.textContent = "Submitting...";
      try {{
        const response = await fetch(path, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          credentials: "same-origin",
          body: JSON.stringify(body),
        }});
        const result = await response.json();
        if (!response.ok) {{
          throw new Error(result.detail || result?.error?.message || `HTTP ${{response.status}}`);
        }}
        actionStatus.textContent = `Success: ${{JSON.stringify(result)}}`;
        form.reset();
        await load();
      }} catch (error) {{
        actionStatus.textContent = `Error: ${{error.message}}`;
      }}
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
    document.addEventListener("submit", event => {{
      if (event.target.classList.contains("action-form")) submitAction(event);
    }});
    renderActions();
    load();
  </script>
</body>
</html>"""
    )

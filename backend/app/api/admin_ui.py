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
    "overview": ("Overview", "/api/v1/admin/overview", "/admin"),
    "users": ("Users", "/api/v1/admin/users", "/admin/users"),
    "jobs": ("Jobs", "/api/v1/admin/jobs", "/admin/jobs"),
    "payments": ("Payments", "/api/v1/admin/payments", "/admin/payments"),
    "runpod": ("RunPod", "/api/v1/admin/runpod/pods", "/admin/runpod"),
    "business": ("Business Accounts", "/api/v1/admin/business-accounts", "/admin/business"),
    "reports": ("Reports", "/api/v1/admin/reports/finance/daily", "/admin/reports"),
    "reports_users": (
        "User Spending",
        "/api/v1/admin/reports/users/spending",
        "/admin/reports/users",
    ),
    "reports_business": (
        "Business Spending",
        "/api/v1/admin/reports/business/spending",
        "/admin/reports/business",
    ),
    "audit": ("Audit Logs", "/api/v1/admin/audit-logs", "/admin/audit"),
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


@router.get("/reports", response_class=HTMLResponse)
async def admin_reports(_: AdminDep) -> HTMLResponse:
    return _admin_page("reports")


@router.get("/reports/users", response_class=HTMLResponse)
async def admin_user_reports(_: AdminDep) -> HTMLResponse:
    return _admin_page("reports_users")


@router.get("/reports/business", response_class=HTMLResponse)
async def admin_business_reports(_: AdminDep) -> HTMLResponse:
    return _admin_page("reports_business")


def _admin_page(page: str) -> HTMLResponse:
    title, endpoint, _href = PAGES[page]
    actions_enabled = "true" if get_settings().admin_actions_enabled else "false"
    nav = "\n".join(
        f'<a class="{"active" if key == page else ""}" href="{href}">{label}</a>'
        for key, (label, _endpoint, href) in PAGES.items()
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
    const reportPages = new Set(["reports", "reports_users", "reports_business"]);
    const content = document.getElementById("content");
    const actions = document.getElementById("actions");
    const actionStatus = document.getElementById("action-status");
    const refresh = document.getElementById("refresh");
    let businessAccounts = [];

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

    function renderQueueLoadPlan(plan) {{
      if (!plan) return "";
      const warning = Number(plan.recommended_additional_pods || 0) > 0
        ? '<p class="error">Queue load is above target. Add pods before retrying jobs.</p>'
        : "";
      return `
        <h3>Queue load plan</h3>
        ${{warning}}
        <table class="kv"><tbody>
          <tr><th>waiting jobs</th><td>${{escapeHtml(plan.waiting_for_pod_jobs_count)}}</td></tr>
          <tr>
            <th>waiting total minutes</th>
            <td>${{escapeHtml(plan.total_waiting_audio_minutes)}}</td>
          </tr>
          <tr><th>healthy pods</th><td>${{escapeHtml(plan.healthy_pods_count)}}</td></tr>
          <tr><th>idle healthy pods</th><td>${{escapeHtml(plan.idle_healthy_pods_count)}}</td></tr>
          <tr><th>busy pods</th><td>${{escapeHtml(plan.busy_pods_count)}}</td></tr>
          <tr>
            <th>target minutes per pod</th>
            <td>
              ${{escapeHtml(plan.target_minutes_per_pod_min)}}–${{escapeHtml(plan.target_minutes_per_pod_max)}}
            </td>
          </tr>
          <tr>
            <th>recommended additional pods</th>
            <td>${{escapeHtml(plan.recommended_additional_pods)}}</td>
          </tr>
        </tbody></table>`;
    }}

    function renderRunPodSummary(data) {{
      if (page !== "runpod") return "";
      const manualOnly = Boolean(data.manual_only_mode);
      const hint = manualOnly
        ? '<p class="error">Manual-only mode is enabled. '
          + 'Sync manually created RunPod pods before retrying jobs.</p>'
        : "";
      return `
        <h3>RunPod mode</h3>
        ${{hint}}
        <table class="kv"><tbody>
          <tr>
            <th>auto-create enabled</th>
            <td>${{escapeHtml(data.runpod_auto_create_enabled)}}</td>
          </tr>
          <tr><th>starting pods</th><td>${{escapeHtml(data.starting_count)}}</td></tr>
          <tr><th>manual-only mode</th><td>${{escapeHtml(manualOnly)}}</td></tr>
        </tbody></table>`;
    }}

    function formatMoney(value) {{
      const numberValue = Number(value || 0);
      return Number.isFinite(numberValue) ? numberValue.toFixed(2) : String(value ?? "0");
    }}

    function businessAccountOptionLabel(account) {{
      return `${{account.name || "Unnamed"}} — $${{formatMoney(account.available_usd)}}`
        + ` available — ${{account.status || "unknown"}}`;
    }}

    function businessAccountOptions() {{
      if (!businessAccounts.length) {{
        return '<option value="">No business accounts yet. Create one first.</option>';
      }}
      return businessAccounts.map(account =>
        `<option value="${{escapeHtml(account.id)}}">`
        + `${{escapeHtml(businessAccountOptionLabel(account))}}</option>`
      ).join("");
    }}

    function formatValue(value) {{
      if (value === null || value === undefined) return "";
      if (Array.isArray(value)) return `${{value.length}} item(s)`;
      if (typeof value === "object") return JSON.stringify(value);
      return value;
    }}

    function render(data) {{
      if (Array.isArray(data.items)) {{
        return `${{renderRunPodSummary(data)}}`
          + `${{renderQueueLoadPlan(data.queue_load_plan)}}${{renderTable(data.items)}}`;
      }}
      if (Array.isArray(data.pods)) return renderTable(data.pods);
      const rows = flatten(data).filter(([key]) => !key.startsWith("queue_load_plan"))
        .map(([key, value]) =>
        `<tr><th>${{escapeHtml(key)}}</th><td>${{escapeHtml(formatValue(value))}}</td></tr>`
      ).join("");
      return `${{renderQueueLoadPlan(data.queue_load_plan)}}`
        + `<table class="kv"><tbody>${{rows}}</tbody></table>`;
    }}

    function renderActions() {{
      if (reportPages.has(page)) {{
        const billingSelect = `
          <select name="billing_account_type">
            <option value="all">all billing</option>
            <option value="personal">personal</option>
            <option value="business">business</option>
          </select>`;
        let extra = "";
        let csvPath = "/api/v1/admin/reports/finance/daily.csv";
        if (page === "reports") {{
          extra = `${{billingSelect}}
            <input name="user_id" placeholder="User UUID">
            <input name="business_account_id" placeholder="Business account UUID">`;
        }} else if (page === "reports_users") {{
          csvPath = "/api/v1/admin/reports/users/spending.csv";
          extra = `${{billingSelect}}
            <input name="user_id" placeholder="User UUID">
            <input name="telegram_id" placeholder="Telegram ID">
            <input name="limit" placeholder="Limit" value="100">`;
        }} else {{
          csvPath = "/api/v1/admin/reports/business/spending.csv";
          extra = `
            <input name="business_account_id" placeholder="Business account UUID">
            <input name="user_id" placeholder="User UUID">
            <input name="limit" placeholder="Limit" value="100">`;
        }}
        actions.innerHTML = `
          <h3>Filters</h3>
          <form id="report-filter" class="filter-form">
            <input name="date_from" type="date">
            <input name="date_to" type="date">
            ${{extra}}
            <button type="submit">Apply</button>
            <a id="csv-export" class="button-link" href="${{csvPath}}">Download CSV</a>
          </form>`;
        return;
      }}
      if (!actionsEnabled) {{
        actions.innerHTML = '<p class="muted">Admin actions are disabled.</p>';
        return;
      }}
      const accountSelect = `
        <select name="business_account_id" required>
          ${{businessAccountOptions()}}
        </select>`;
      const noBusinessAccounts = !businessAccounts.length && page === "business"
        ? '<p class="muted">No business accounts yet. Create one first.</p>'
        : "";
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
            <input name="user_id" placeholder="Internal user UUID or Telegram ID" required>
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
        payments: `
          <h3>Payment controls</h3>
          <form class="action-form" data-action="payment_recheck">
            <input name="payment_id" placeholder="Payment UUID" required>
            <input name="reason" placeholder="Reason" value="Recheck CryptoBot invoice" required>
            <button type="submit">Recheck payment</button>
          </form>
          <p class="muted">Use this for pending CryptoBot invoices after webhook downtime.</p>`,
        runpod: `
          <h3>RunPod controls</h3>
          <form class="action-form" data-action="sync_runpod_pods">
            <input name="reason" placeholder="Reason"
              value="Sync manually created RunPod pods" required>
            <button type="submit">Sync from RunPod</button>
          </form>
          <form class="action-form" data-action="check_runpod_health">
            <input name="reason" placeholder="Reason" value="Check RunPod pod health" required>
            <button type="submit">Check health</button>
          </form>
          <form class="action-form" data-action="cleanup_idle_pods">
            <input name="reason" placeholder="Reason"
              value="Cleanup expired idle RunPod pods" required>
            <button type="submit">Cleanup idle pods</button>
          </form>
          <form class="action-form" data-action="retry_waiting">
            <input name="reason" placeholder="Reason"
              value="Retry waiting jobs after RunPod sync" required>
            <button type="submit">Retry waiting jobs</button>
          </form>
          <form class="action-form" data-action="terminate_pod">
            <input name="runpod_pod_id" placeholder="RunPod pod id" required>
            <input name="reason" placeholder="Reason" required>
            <button type="submit">Terminate pod</button>
          </form>`,
        business: `
          ${{noBusinessAccounts}}
          <h3>Create business account</h3>
          <form class="action-form" data-action="business_create">
            <input name="name" placeholder="FireTraff" required>
            <input name="owner_telegram_id" placeholder="Owner Telegram ID">
            <input name="owner_user_id" placeholder="Owner User UUID">
            <input name="initial_balance_usd" placeholder="Initial balance USD" value="0">
            <input name="reason" placeholder="Reason" required>
            <button type="submit">Create business account</button>
          </form>
          <h3>Business controls</h3>
          <form class="action-form" data-action="business_topup">
            ${{accountSelect}}
            <input name="amount_usd" placeholder="Amount USD" required>
            <input name="reason" placeholder="Reason" required>
            <button type="submit">Top up business balance</button>
          </form>
          <form class="action-form" data-action="business_add_member">
            ${{accountSelect}}
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
            ${{accountSelect}}
            <input name="user_id" placeholder="User UUID" required>
            <input name="reason" placeholder="Reason" required>
            <button type="submit">Deactivate member</button>
          </form>`,
      }};
      actions.innerHTML = forms[page] || '<p class="muted">No actions for this page.</p>';
    }}

    function reportQuery() {{
      if (!reportPages.has(page)) return "";
      const form = document.getElementById("report-filter");
      if (!form) return "";
      const params = new URLSearchParams();
      for (const [key, value] of new FormData(form).entries()) {{
        if (value !== "") params.set(key, value);
      }}
      const query = params.toString();
      return query ? `?${{query}}` : "";
    }}

    function updateCsvLink(query) {{
      const link = document.getElementById("csv-export");
      if (!link) return;
      const base = page === "reports"
        ? "/api/v1/admin/reports/finance/daily.csv"
        : page === "reports_users"
          ? "/api/v1/admin/reports/users/spending.csv"
          : "/api/v1/admin/reports/business/spending.csv";
      link.href = `${{base}}${{query}}`;
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
      }} else if (form.dataset.action === "sync_runpod_pods") {{
        path = "/api/v1/admin/runpod/pods/sync";
      }} else if (form.dataset.action === "check_runpod_health") {{
        path = "/api/v1/admin/runpod/pods/check-health";
      }} else if (form.dataset.action === "cleanup_idle_pods") {{
        path = "/api/v1/admin/runpod/pods/cleanup-idle";
      }} else if (form.dataset.action === "personal_topup") {{
        path = `/api/v1/admin/users/${{data.user_id}}/balance/top-up`;
        body.amount_usd = data.amount_usd;
      }} else if (form.dataset.action === "user_block") {{
        path = `/api/v1/admin/users/${{data.user_id}}/block`;
      }} else if (form.dataset.action === "user_unblock") {{
        path = `/api/v1/admin/users/${{data.user_id}}/unblock`;
      }} else if (form.dataset.action === "fail_refund") {{
        path = `/api/v1/admin/jobs/${{data.job_id}}/fail-refund`;
      }} else if (form.dataset.action === "payment_recheck") {{
        path = `/api/v1/admin/payments/${{data.payment_id}}/recheck`;
      }} else if (form.dataset.action === "terminate_pod") {{
        path = `/api/v1/admin/runpod/pods/${{data.runpod_pod_id}}/terminate`;
      }} else if (form.dataset.action === "business_create") {{
        path = "/api/v1/admin/business-accounts";
        body.name = data.name;
        body.owner_telegram_id = data.owner_telegram_id
          ? Number(data.owner_telegram_id)
          : undefined;
        body.owner_user_id = data.owner_user_id;
        body.initial_balance_usd = data.initial_balance_usd || "0";
      }} else if (form.dataset.action === "business_topup") {{
        if (!data.business_account_id) {{
          actionStatus.textContent = "Error: Create/select a business account first";
          return;
        }}
        path = `/api/v1/admin/business-accounts/${{data.business_account_id}}/balance/top-up`;
        body.amount_usd = data.amount_usd;
      }} else if (form.dataset.action === "business_add_member") {{
        if (!data.business_account_id) {{
          actionStatus.textContent = "Error: Create/select a business account first";
          return;
        }}
        path = `/api/v1/admin/business-accounts/${{data.business_account_id}}/members`;
        body.telegram_id = data.telegram_id ? Number(data.telegram_id) : undefined;
        body.user_id = data.user_id;
        body.role = data.role || "member";
      }} else if (form.dataset.action === "business_remove_member") {{
        if (!data.business_account_id) {{
          actionStatus.textContent = "Error: Create/select a business account first";
          return;
        }}
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
        if (form.dataset.action === "business_create") {{
          actionStatus.textContent = `Success: ${{result.business_account_name}} `
            + `(${{result.business_account_id}})`;
        }} else {{
          actionStatus.textContent = `Success: ${{JSON.stringify(result)}}`;
        }}
        form.reset();
        await load();
      }} catch (error) {{
        actionStatus.textContent = `Error: ${{error.message}}`;
      }}
    }}

    async function load() {{
      content.innerHTML = '<p class="muted">Loading...</p>';
      try {{
        const query = reportQuery();
        updateCsvLink(query);
        if (page === "reports") {{
          const [summaryResponse, dailyResponse] = await Promise.all([
            fetch(
              `/api/v1/admin/reports/finance/summary${{query}}`,
              {{ credentials: "same-origin" }},
            ),
            fetch(`${{endpoint}}${{query}}`, {{ credentials: "same-origin" }}),
          ]);
          if (!summaryResponse.ok) throw new Error(`Summary HTTP ${{summaryResponse.status}}`);
          if (!dailyResponse.ok) throw new Error(`Daily HTTP ${{dailyResponse.status}}`);
          const summary = await summaryResponse.json();
          const daily = await dailyResponse.json();
          content.innerHTML = `<h3>Summary</h3>${{render(summary.totals)}}`
            + `<h3>Daily</h3>${{renderTable(daily.items || [])}}`;
          return;
        }}
        const response = await fetch(`${{endpoint}}${{query}}`, {{ credentials: "same-origin" }});
        if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
        const data = await response.json();
        if (page === "business") {{
          businessAccounts = data.items || [];
          renderActions();
        }}
        content.innerHTML = render(data);
      }} catch (error) {{
        content.innerHTML = `<p class="error">Failed to load: ${{escapeHtml(error.message)}}</p>`;
      }}
    }}

    refresh.addEventListener("click", load);
    document.addEventListener("submit", event => {{
      if (event.target.classList.contains("action-form")) submitAction(event);
      if (event.target.id === "report-filter") {{
        event.preventDefault();
        load();
      }}
    }});
    renderActions();
    load();
  </script>
</body>
</html>"""
    )

"""
MCP Server — AgentMail + HubSpot
Exposes tools for reading/sending emails and managing HubSpot contacts & deals.
Transport: HTTP + SSE (for cloud deployment)
"""

import os
import json
import requests
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route, Mount
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent

# ── Clients ────────────────────────────────────────────────────────────────────

AGENTMAIL_API_KEY = os.environ["AGENTMAIL_API_KEY"]
AGENTMAIL_INBOX_ID = os.environ["AGENTMAIL_INBOX_ID"]   # your inbox id, e.g. hello@agentmail.to
HUBSPOT_ACCESS_TOKEN = os.environ["HUBSPOT_ACCESS_TOKEN"]

agentmail_headers = {
    "Authorization": f"Bearer {AGENTMAIL_API_KEY}",
    "Content-Type": "application/json",
}
hubspot_headers = {
    "Authorization": f"Bearer {HUBSPOT_ACCESS_TOKEN}",
    "Content-Type": "application/json",
}

# ── MCP Server ─────────────────────────────────────────────────────────────────

mcp = Server("agentmail-hubspot")

# ── Tool definitions ───────────────────────────────────────────────────────────

@mcp.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_emails",
            description="List recent emails from your AgentMail inbox.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of emails to return (default 10, max 50).",
                        "default": 10,
                    }
                },
            },
        ),
        Tool(
            name="send_email",
            description="Send an email from your AgentMail inbox.",
            inputSchema={
                "type": "object",
                "required": ["to", "subject", "text"],
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address."},
                    "subject": {"type": "string", "description": "Email subject line."},
                    "text": {"type": "string", "description": "Plain-text email body."},
                    "html": {"type": "string", "description": "Optional HTML email body."},
                },
            },
        ),
        Tool(
            name="create_hubspot_contact",
            description="Create a new contact in HubSpot CRM.",
            inputSchema={
                "type": "object",
                "required": ["email"],
                "properties": {
                    "email": {"type": "string", "description": "Contact email address."},
                    "firstname": {"type": "string", "description": "First name."},
                    "lastname": {"type": "string", "description": "Last name."},
                    "phone": {"type": "string", "description": "Phone number."},
                    "company": {"type": "string", "description": "Company name."},
                },
            },
        ),
        Tool(
            name="create_hubspot_deal",
            description="Create a new deal in HubSpot CRM.",
            inputSchema={
                "type": "object",
                "required": ["dealname"],
                "properties": {
                    "dealname": {"type": "string", "description": "Name of the deal."},
                    "amount": {"type": "string", "description": "Deal value (e.g. '5000')."},
                    "dealstage": {
                        "type": "string",
                        "description": "Deal stage (e.g. 'appointmentscheduled', 'contractsent', 'closedwon').",
                        "default": "appointmentscheduled",
                    },
                    "closedate": {
                        "type": "string",
                        "description": "Expected close date in ISO 8601 format (e.g. '2025-12-31T00:00:00Z').",
                    },
                    "pipeline": {
                        "type": "string",
                        "description": "Pipeline ID (use 'default' if unsure).",
                        "default": "default",
                    },
                },
            },
        ),
    ]

# ── Tool implementations ────────────────────────────────────────────────────────

@mcp.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "list_emails":
        return await tool_list_emails(arguments)
    elif name == "send_email":
        return await tool_send_email(arguments)
    elif name == "create_hubspot_contact":
        return await tool_create_hubspot_contact(arguments)
    elif name == "create_hubspot_deal":
        return await tool_create_hubspot_deal(arguments)
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def tool_list_emails(args: dict) -> list[TextContent]:
    limit = min(args.get("limit", 10), 50)
    inbox_id = AGENTMAIL_INBOX_ID
    url = f"https://api.agentmail.to/v0/inboxes/{inbox_id}/messages"
    resp = requests.get(url, headers=agentmail_headers, params={"limit": limit})
    if not resp.ok:
        return [TextContent(type="text", text=f"AgentMail error {resp.status_code}: {resp.text}")]
    data = resp.json()
    messages = data.get("messages", data) if isinstance(data, dict) else data
    if not messages:
        return [TextContent(type="text", text="No emails found.")]
    lines = []
    for msg in messages[:limit]:
        lines.append(
            f"From: {msg.get('from', 'unknown')} | "
            f"Subject: {msg.get('subject', '(no subject)')} | "
            f"Date: {msg.get('date', '')} | "
            f"ID: {msg.get('message_id', msg.get('id', ''))}"
        )
    return [TextContent(type="text", text="\n".join(lines))]


async def tool_send_email(args: dict) -> list[TextContent]:
    inbox_id = AGENTMAIL_INBOX_ID
    url = f"https://api.agentmail.to/v0/inboxes/{inbox_id}/messages"
    payload = {
        "to": args["to"],
        "subject": args["subject"],
        "text": args["text"],
    }
    if "html" in args:
        payload["html"] = args["html"]
    resp = requests.post(url, headers=agentmail_headers, json=payload)
    if not resp.ok:
        return [TextContent(type="text", text=f"AgentMail error {resp.status_code}: {resp.text}")]
    return [TextContent(type="text", text=f"Email sent to {args['to']} successfully.")]


async def tool_create_hubspot_contact(args: dict) -> list[TextContent]:
    url = "https://api.hubapi.com/crm/v3/objects/contacts"
    properties = {k: v for k, v in args.items()}
    resp = requests.post(url, headers=hubspot_headers, json={"properties": properties})
    if resp.status_code == 409:
        return [TextContent(type="text", text=f"Contact with email {args['email']} already exists in HubSpot.")]
    if not resp.ok:
        return [TextContent(type="text", text=f"HubSpot error {resp.status_code}: {resp.text}")]
    contact = resp.json()
    return [TextContent(type="text", text=f"Contact created. HubSpot ID: {contact['id']}")]


async def tool_create_hubspot_deal(args: dict) -> list[TextContent]:
    url = "https://api.hubapi.com/crm/v3/objects/deals"
    properties = {
        "dealname": args["dealname"],
        "pipeline": args.get("pipeline", "default"),
        "dealstage": args.get("dealstage", "appointmentscheduled"),
    }
    if "amount" in args:
        properties["amount"] = args["amount"]
    if "closedate" in args:
        properties["closedate"] = args["closedate"]
    resp = requests.post(url, headers=hubspot_headers, json={"properties": properties})
    if not resp.ok:
        return [TextContent(type="text", text=f"HubSpot error {resp.status_code}: {resp.text}")]
    deal = resp.json()
    return [TextContent(type="text", text=f"Deal '{args['dealname']}' created. HubSpot ID: {deal['id']}")]

# ── Landing page ───────────────────────────────────────────────────────────────

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>AgentMail + HubSpot MCP Server</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #0f0f11; color: #e8e6e1; min-height: 100vh;
           display: flex; flex-direction: column; align-items: center;
           padding: 64px 24px; }
    .badge { background: #1a1a2e; border: 1px solid #3b3b5c; color: #7b7bff;
             font-size: 12px; font-weight: 600; letter-spacing: .08em;
             text-transform: uppercase; padding: 4px 12px; border-radius: 20px;
             margin-bottom: 24px; display: inline-block; }
    h1 { font-size: clamp(28px, 5vw, 48px); font-weight: 700; letter-spacing: -.02em;
         line-height: 1.15; text-align: center; max-width: 640px;
         background: linear-gradient(135deg, #fff 40%, #888); -webkit-background-clip: text;
         -webkit-text-fill-color: transparent; margin-bottom: 16px; }
    .sub { color: #888; font-size: 18px; text-align: center; max-width: 520px;
           line-height: 1.6; margin-bottom: 48px; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
             gap: 16px; width: 100%; max-width: 900px; margin-bottom: 48px; }
    .card { background: #18181c; border: 1px solid #2a2a2e; border-radius: 12px;
            padding: 24px; }
    .card .icon { font-size: 28px; margin-bottom: 12px; }
    .card h3 { font-size: 15px; font-weight: 600; margin-bottom: 6px; color: #fff; }
    .card p  { font-size: 13px; color: #888; line-height: 1.55; }
    .endpoint-box { background: #18181c; border: 1px solid #2a2a2e; border-radius: 12px;
                    padding: 24px; width: 100%; max-width: 900px; margin-bottom: 32px; }
    .endpoint-box h2 { font-size: 14px; font-weight: 600; color: #888;
                       text-transform: uppercase; letter-spacing: .08em; margin-bottom: 16px; }
    .ep { display: flex; align-items: center; gap: 12px; padding: 10px 14px;
          background: #0f0f11; border: 1px solid #2a2a2e; border-radius: 8px;
          margin-bottom: 8px; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; }
    .method { background: #1a2e1a; color: #4caf50; padding: 2px 8px;
              border-radius: 4px; font-size: 11px; font-weight: 700; }
    .ep .path { color: #ccc; }
    .ep .desc { color: #666; margin-left: auto; font-size: 12px; font-family: sans-serif; }
    footer { color: #444; font-size: 13px; text-align: center; margin-top: 16px; }
    a { color: #7b7bff; text-decoration: none; }
    a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <span class="badge">MCP Server</span>
  <h1>AgentMail + HubSpot Integration</h1>
  <p class="sub">A Model Context Protocol server that lets AI agents read &amp; send email via AgentMail and manage contacts &amp; deals in HubSpot CRM.</p>

  <div class="cards">
    <div class="card">
      <div class="icon">📬</div>
      <h3>list_emails</h3>
      <p>Fetch recent messages from an AgentMail inbox with configurable limit.</p>
    </div>
    <div class="card">
      <div class="icon">✉️</div>
      <h3>send_email</h3>
      <p>Send plain-text or HTML emails from a managed AgentMail inbox.</p>
    </div>
    <div class="card">
      <div class="icon">👤</div>
      <h3>create_hubspot_contact</h3>
      <p>Create a new contact in HubSpot CRM with name, email, phone and company.</p>
    </div>
    <div class="card">
      <div class="icon">💼</div>
      <h3>create_hubspot_deal</h3>
      <p>Create deals in HubSpot with stage, pipeline, amount and close date.</p>
    </div>
  </div>

  <div class="endpoint-box">
    <h2>Endpoints</h2>
    <div class="ep"><span class="method">GET</span><span class="path">/</span><span class="desc">This page</span></div>
    <div class="ep"><span class="method">GET</span><span class="path">/sse</span><span class="desc">MCP SSE connection</span></div>
    <div class="ep"><span class="method">POST</span><span class="path">/messages</span><span class="desc">MCP message endpoint</span></div>
    <div class="ep"><span class="method">GET</span><span class="path">/health</span><span class="desc">Health check → 200 OK</span></div>
  </div>

  <footer>Built with <a href="https://modelcontextprotocol.io" target="_blank">Model Context Protocol</a> · <a href="https://agentmail.to" target="_blank">AgentMail</a> · <a href="https://hubspot.com" target="_blank">HubSpot</a></footer>
</body>
</html>"""

# ── Starlette app ──────────────────────────────────────────────────────────────

sse_transport = SseServerTransport("/messages")

async def handle_sse(request: Request):
    async with sse_transport.connect_sse(request.scope, request.receive, request._send) as streams:
        await mcp.run(streams[0], streams[1], mcp.create_initialization_options())

async def landing(request: Request):
    return HTMLResponse(LANDING_HTML)

async def health(request: Request):
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok"})

app = Starlette(
    routes=[
        Route("/", landing),
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

"""
MCP Server — Personal Agents MCP (AgentMail + HubSpot)
Production-style MCP server with structured logging.
By Richard Tanksley
"""

import os
import json
import uuid
import time
import logging
import requests
import uvicorn

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, Mount

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent

# ── Structured Logging ────────────────────────────────────────────────────────

class StructuredLogger:
    def __init__(self):
        self.logger = logging.getLogger("personal-agents-mcp")
        self.logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.handlers = [handler]

    def log(self, event: str, **kwargs):
        payload = {
            "timestamp": time.time(),
            "service": "personal-agents-mcp",
            "event": event,
            **kwargs,
        }
        self.logger.info(json.dumps(payload))


log = StructuredLogger()

# ── Environment Loader ───────────────────────────────────────────────────────

def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        log.log("env_missing", variable=name)
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

AGENTMAIL_API_KEY = require_env("AGENTMAIL_API_KEY")
# Defaulting to the unified inbox for listing, while keeping a specific ID for sending
AGENTMAIL_INBOX_ID = os.getenv("AGENTMAIL_INBOX_ID") 
AGENTMAIL_UNIFIED_ID = "unified@agentmail.to"
HUBSPOT_ACCESS_TOKEN = require_env("HUBSPOT_ACCESS_TOKEN")

agentmail_headers = {
    "Authorization": f"Bearer {AGENTMAIL_API_KEY}",
    "Content-Type": "application/json",
}

hubspot_headers = {
    "Authorization": f"Bearer {HUBSPOT_ACCESS_TOKEN}",
    "Content-Type": "application/json",
}

# ── MCP Server ────────────────────────────────────────────────────────────────

mcp = Server("personal-agents-mcp")

# ── Tool Definitions ──────────────────────────────────────────────────────────

@mcp.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_emails",
            description="List recent emails across all agents using the unified inbox.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                },
            },
        ),
        Tool(
            name="send_email",
            description="Send email via a specific AgentMail inbox.",
            inputSchema={
                "type": "object",
                "required": ["to", "subject", "text"],
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "text": {"type": "string"},
                    "html": {"type": "string"},
                    "from_inbox": {"type": "string", "description": "Specific inbox ID to send from. Defaults to AGENTMAIL_INBOX_ID env var."},
                },
            },
        ),
        Tool(
            name="create_hubspot_contact",
            description="Create HubSpot contact.",
            inputSchema={
                "type": "object",
                "required": ["email"],
                "properties": {
                    "email": {"type": "string"},
                    "firstname": {"type": "string"},
                    "lastname": {"type": "string"},
                    "phone": {"type": "string"},
                    "company": {"type": "string"},
                },
            },
        ),
        Tool(
            name="create_hubspot_deal",
            description="Create HubSpot deal.",
            inputSchema={
                "type": "object",
                "required": ["dealname"],
                "properties": {
                    "dealname": {"type": "string"},
                    "amount": {"type": "string"},
                    "dealstage": {"type": "string", "default": "appointmentscheduled"},
                    "closedate": {"type": "string"},
                    "pipeline": {"type": "string", "default": "default"},
                },
            },
        ),
    ]

# ── Tool Dispatcher ───────────────────────────────────────────────────────────

@mcp.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    request_id = str(uuid.uuid4())

    log.log(
        "tool_call_start",
        request_id=request_id,
        tool=name,
        arguments=arguments,
    )

    try:
        if name == "list_emails":
            result = await tool_list_emails(arguments)

        elif name == "send_email":
            result = await tool_send_email(arguments)

        elif name == "create_hubspot_contact":
            result = await tool_create_hubspot_contact(arguments)

        elif name == "create_hubspot_deal":
            result = await tool_create_hubspot_deal(arguments)

        else:
            result = [TextContent(type="text", text=f"Unknown tool: {name}")]

        log.log(
            "tool_call_success",
            request_id=request_id,
            tool=name,
            result_preview=str(result)[:300],
        )

        return result

    except Exception as e:
        log.log(
            "tool_call_error",
            request_id=request_id,
            tool=name,
            error=str(e),
        )
        return [TextContent(type="text", text=f"Error: {str(e)}")]

# ── Tool Implementations ──────────────────────────────────────────────────────

async def tool_list_emails(args: dict):
    limit = min(args.get("limit", 10), 50)
    # Using the Unified Inbox address to see all messages across the org
    url = f"https://api.agentmail.to/v0/inboxes/{AGENTMAIL_UNIFIED_ID}/messages"

    log.log("list_emails_request_unified", limit=limit)

    resp = requests.get(url, headers=agentmail_headers, params={"limit": limit})

    if not resp.ok:
        log.log("list_emails_error", status=resp.status_code)
        return [TextContent(type="text", text=f"AgentMail error {resp.status_code}")]

    data = resp.json()
    messages = data.get("messages", data)

    return [
        TextContent(
            type="text",
            text="\n".join(
                f"{m.get('from')} | {m.get('subject')}" for m in messages[:limit]
            ),
        )
    ]


async def tool_send_email(args: dict):
    # Sending requires a specific sender ID, it cannot use 'unified'
    sender_id = args.get("from_inbox") or AGENTMAIL_INBOX_ID
    
    if not sender_id:
        return [TextContent(type="text", text="Error: No sender inbox ID provided.")]

    url = f"https://api.agentmail.to/v0/inboxes/{sender_id}/messages"

    log.log("send_email_request", to=args["to"], from_inbox=sender_id, subject=args["subject"])

    payload = {
        "to": args["to"],
        "subject": args["subject"],
        "text": args["text"],
    }

    if "html" in args:
        payload["html"] = args["html"]

    resp = requests.post(url, headers=agentmail_headers, json=payload)

    if not resp.ok:
        log.log("send_email_error", status=resp.status_code)
        return [TextContent(type="text", text=f"Failed to send email: {resp.status_code}")]

    log.log("send_email_success", to=args["to"])

    return [TextContent(type="text", text="Email sent successfully.")]


async def tool_create_hubspot_contact(args: dict):
    url = "https://api.hubapi.com/crm/v3/objects/contacts"

    log.log("hubspot_contact_create", email=args["email"])

    resp = requests.post(
        url,
        headers=hubspot_headers,
        json={"properties": args},
    )

    if not resp.ok:
        log.log("hubspot_contact_error", status=resp.status_code)
        return [TextContent(type="text", text="HubSpot error")]

    contact = resp.json()

    log.log("hubspot_contact_success", id=contact["id"])

    return [TextContent(type="text", text=f"Contact ID: {contact['id']}")]


async def tool_create_hubspot_deal(args: dict):
    url = "https://api.hubapi.com/crm/v3/objects/deals"

    log.log("hubspot_deal_create", dealname=args["dealname"])

    resp = requests.post(
        url,
        headers=hubspot_headers,
        json={"properties": args},
    )

    if not resp.ok:
        log.log("hubspot_deal_error", status=resp.status_code)
        return [TextContent(type="text", text="HubSpot error")]

    deal = resp.json()

    log.log("hubspot_deal_success", id=deal["id"])

    return [TextContent(type="text", text=f"Deal ID: {deal['id']}")]

# ── Landing Page Loader ──────────────────────────────────────────────────────

def get_landing_content():
    # Use an absolute path based on this file's location to avoid Render path errors
    base_path = os.path.dirname(__file__)
    template_path = os.path.join(base_path, "templates", "landing.html")
    
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        log.log("template_error", path=template_path)
        return "<h1>Server Online</h1><p>Template not found.</p>"

# Load the content once at startup

LANDING_HTML = get_landing_content()

# ── App ───────────────────────────────────────────────────────────────────────

sse_transport = SseServerTransport("/messages")

async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope,
        request.receive,
        request._send
    ) as streams:
        await mcp.run(streams, streams, mcp.create_initialization_options())

async def landing(request: Request):
    return HTMLResponse(LANDING_HTML)

async def health(request: Request):
    return JSONResponse({"status": "ok"})

app = Starlette(
    routes=[
        Route("/", landing),
        Route("/health", health),
        Route("/sse", handle_sse),
        Route("/messages", endpoint=sse_transport.handle_post_message, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory="static"), name="static"),
    ]
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    log.log("server_starting", port=port)
    uvicorn.run(app, host="0.0.0.0", port=port)

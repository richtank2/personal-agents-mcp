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
from starlette.staticfiles import StaticFiles

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
AGENTMAIL_UNIFIED_ID = "unified@agentmail.to"
# Default sender address
AGENTMAIL_INBOX_ID = "sillybar537@agentmail.to"

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
            description=f"Send email via AgentMail (Defaults to {AGENTMAIL_INBOX_ID}).",
            inputSchema={
                "type": "object",
                "required": ["to", "subject", "text"],
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "text": {"type": "string"},
                    "html": {"type": "string"},
                    "from_inbox": {"type": "string", "description": "Override default sender inbox ID."},
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
    log.log("tool_call_start", request_id=request_id, tool=name, arguments=arguments)

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

        log.log("tool_call_success", request_id=request_id, tool=name)
        return result

    except Exception as e:
        log.log("tool_call_error", request_id=request_id, tool=name, error=str(e))
        return [TextContent(type="text", text=f"Error: {str(e)}")]

# ── Tool Implementations ──────────────────────────────────────────────────────

async def tool_list_emails(args: dict):
    limit = min(args.get("limit", 10), 50)
    url = f"https://api.agentmail.to/v0/inboxes/{AGENTMAIL_UNIFIED_ID}/messages"
    resp = requests.get(url, headers=agentmail_headers, params={"limit": limit})
    if not resp.ok:
        return [TextContent(type="text", text=f"AgentMail error {resp.status_code}")]
    data = resp.json()
    messages = data.get("messages", data)
    return [TextContent(type="text", text="\n".join(f"{m.get('from')} | {m.get('subject')}" for m in messages))]

async def tool_send_email(args: dict):
    sender_id = args.get("from_inbox") or AGENTMAIL_INBOX_ID
    url = f"https://api.agentmail.to/v0/inboxes/{sender_id}/messages"
    payload = {"to": args["to"], "subject": args["subject"], "text": args["text"]}
    if "html" in args: payload["html"] = args["html"]
    resp = requests.post(url, headers=agentmail_headers, json=payload)
    return [TextContent(type="text", text="Email sent successfully." if resp.ok else "Failed to send email")]

async def tool_create_hubspot_contact(args: dict):
    url = "https://api.hubapi.com/crm/v3/objects/contacts"
    resp = requests.post(url, headers=hubspot_headers, json={"properties": args})
    return [TextContent(type="text", text=f"Contact ID: {resp.json().get('id')}" if resp.ok else "HubSpot error")]

async def tool_create_hubspot_deal(args: dict):
    url = "https://api.hubapi.com/crm/v3/objects/deals"
    resp = requests.post(url, headers=hubspot_headers, json={"properties": args})
    return [TextContent(type="text", text=f"Deal ID: {resp.json().get('id')}" if resp.ok else "HubSpot error")]

# ── Landing Page Loader ──────────────────────────────────────────────────────

def get_landing_content():
    base_path = os.path.dirname(__file__)
    template_path = os.path.join(base_path, "templates", "landing.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        log.log("template_error", path=template_path)
        return "<h1>Server Online</h1><p>Template not found.</p>"

LANDING_HTML = get_landing_content()

# ── App ───────────────────────────────────────────────────────────────────────

sse_transport = SseServerTransport("/messages")

async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope,
        request.receive,
        request._send
    ) as streams:
        # Correctly passing read and write streams
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

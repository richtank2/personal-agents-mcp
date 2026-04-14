"""
MCP Server — Personal Agents MCP (AgentMail + HubSpot)
v.1.4
Production-style MCP server with structured logging and Bearer Security.
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
from starlette.responses import HTMLResponse, JSONResponse, Response
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

# Security Token for MCP Authentication
MCP_ACCESS_TOKEN = require_env("MCP_ACCESS_TOKEN")

# API Credentials
AGENTMAIL_API_KEY = require_env("AGENTMAIL_API_KEY")
HUBSPOT_ACCESS_TOKEN = require_env("HUBSPOT_ACCESS_TOKEN")

# Constants
AGENTMAIL_UNIFIED_ID = "unified@agentmail.to"
AGENTMAIL_INBOX_ID = "sillybar537@agentmail.to"

# Global Headers
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
            name="hubspot_request",
            description="Universal tool to access any HubSpot CRM V3 API endpoint. DELETE is prohibited.",
            inputSchema={
                "type": "object",
                "required": ["method", "path"],
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PATCH", "PUT"]},
                    "path": {"type": "string", "description": "The API path, e.g., 'crm/v3/objects/contacts'"},
                    "body": {"type": "object", "description": "The JSON payload for the request"}
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
        elif name == "hubspot_request":
            result = await tool_hubspot_universal_proxy(arguments)
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
        return [TextContent(type="text", text=f"AgentMail Error: {resp.status_code}")]
    
    data = resp.json()
    messages = data.get("messages", [])
    output = "\n".join([f"- From: {m.get('from')} | Subject: {m.get('subject')}" for m in messages])
    return [TextContent(type="text", text=output or "No messages found.")]

async def tool_send_email(args: dict):
    sender_id = args.get("from_inbox") or AGENTMAIL_INBOX_ID
    url = f"https://api.agentmail.to/v0/inboxes/{sender_id}/messages"
    payload = {"to": args["to"], "subject": args["subject"], "text": args["text"]}
    if "html" in args: payload["html"] = args["html"]
    
    resp = requests.post(url, headers=agentmail_headers, json=payload)
    status = "successfully sent" if resp.ok else f"failed with status {resp.status_code}"
    return [TextContent(type="text", text=f"Email {status}.")]

async def tool_hubspot_universal_proxy(args: dict):
    method = args["method"].upper()
    if method == "DELETE":
        return [TextContent(type="text", text="Error: DELETE operations are prohibited.")]
    
    path = args["path"].lstrip("/")
    url = f"https://api.hubapi.com/{path}"
    
    resp = requests.request(
        method=method,
        url=url,
        headers=hubspot_headers,
        json=args.get("body", {})
    )

    try:
        return [TextContent(type="text", text=json.dumps(resp.json(), indent=2))]
    except:
        return [TextContent(type="text", text=f"Request status: {resp.status_code}")]

# ── App Logic ────────────────────────────────────────────────────────────────

sse_transport = SseServerTransport("/messages")

async def handle_sse(request: Request):
    # Security: Verify Bearer Token
    auth_header = request.headers.get("Authorization")
    expected_auth = f"Bearer {MCP_ACCESS_TOKEN}"

    if not auth_header or auth_header != expected_auth:
        log.log("unauthorized_access_attempt", ip=request.client.host)
        return Response("Unauthorized", status_code=401)

    async with sse_transport.connect_sse(
        request.scope,
        request.receive,
        request._send
    ) as streams:
        await mcp.run(streams, streams, mcp.create_initialization_options())

async def landing(request: Request):
    base_path = os.path.dirname(__file__)
    template_path = os.path.join(base_path, "templates", "landing.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Server Online</h1><p>Portfolio template not found.</p>")

app = Starlette(
    routes=[
        Route("/", landing),
        Route("/health", lambda r: JSONResponse({"status": "ok"})),
        Route("/sse", handle_sse),
        Route("/messages", endpoint=sse_transport.handle_post_message, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory="static"), name="static"),
    ]
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    log.log("server_starting", port=port)
    uvicorn.run(app, host="0.0.0.0", port=port)

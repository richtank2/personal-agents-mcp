"""
MCP Server — Personal Agents MCP (AgentMail + HubSpot)
Fixed SSE Stream Unpacking for Manus AI Compatibility
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
        payload = {"timestamp": time.time(), "service": "personal-agents-mcp", "event": event, **kwargs}
        self.logger.info(json.dumps(payload))

log = StructuredLogger()

# ── Environment Loader ───────────────────────────────────────────────────────

def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        log.log("env_missing", variable=name)
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

MCP_ACCESS_TOKEN = require_env("MCP_ACCESS_TOKEN")
AGENTMAIL_API_KEY = require_env("AGENTMAIL_API_KEY")
HUBSPOT_ACCESS_TOKEN = require_env("HUBSPOT_ACCESS_TOKEN")

AGENTMAIL_UNIFIED_ID = "unified@agentmail.to"
AGENTMAIL_INBOX_ID = "sillybar537@agentmail.to"

agentmail_headers = {"Authorization": f"Bearer {AGENTMAIL_API_KEY}", "Content-Type": "application/json"}
hubspot_headers = {"Authorization": f"Bearer {HUBSPOT_ACCESS_TOKEN}", "Content-Type": "application/json"}

# ── MCP Server ────────────────────────────────────────────────────────────────

mcp = Server("personal-agents-mcp")

# ── Tool Implementations ──────────────────────────────────────────────────────

async def tool_list_emails(args: dict):
    limit = min(args.get("limit", 5), 50)
    url = f"https://api.agentmail.to/v0/inboxes/{AGENTMAIL_UNIFIED_ID}/messages"
    resp = requests.get(url, headers=agentmail_headers, params={"limit": limit})
    if not resp.ok: return [TextContent(type="text", text=f"AgentMail Error: {resp.status_code}")]
    
    messages = resp.json().get("messages", [])
    if not messages: return [TextContent(type="text", text="No emails found.")]

    formatted_messages = []
    for m in messages:
        msg_detail = (
            f"--- EMAIL START ---\nID: {m.get('id')}\nFrom: {m.get('from')}\n"
            f"Subject: {m.get('subject')}\nBody:\n{m.get('text', m.get('body', '[No Content]'))}\n"
            f"--- EMAIL END ---\n"
        )
        formatted_messages.append(msg_detail)
    return [TextContent(type="text", text="\n".join(formatted_messages))]

async def tool_send_email(args: dict):
    sender_id = args.get("from_inbox") or AGENTMAIL_INBOX_ID
    url = f"https://api.agentmail.to/v0/inboxes/{sender_id}/messages"
    payload = {"to": args["to"], "subject": args["subject"], "text": args["text"]}
    if "html" in args: payload["html"] = args["html"]
    resp = requests.post(url, headers=agentmail_headers, json=payload)
    return [TextContent(type="text", text="Success" if resp.ok else f"Failed: {resp.status_code}")]

async def tool_hubspot_universal_proxy(args: dict):
    method = args["method"].upper()
    if method == "DELETE": return [TextContent(type="text", text="Error: DELETE prohibited.")]
    url = f"https://api.hubapi.com/{args['path'].lstrip('/')}"
    resp = requests.request(method=method, url=url, headers=hubspot_headers, json=args.get("body", {}))
    return [TextContent(type="text", text=json.dumps(resp.json(), indent=2))]

# ── MCP Callbacks ───────────────────────────────────────────────────────────

@mcp.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="list_emails", description="Read recent emails with full bodies.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 5}}}),
        Tool(name="send_email", description="Send email via AgentMail.", inputSchema={"type": "object", "required": ["to", "subject", "text"], "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "text": {"type": "string"}}}),
        Tool(name="hubspot_request", description="Universal HubSpot API proxy.", inputSchema={"type": "object", "required": ["method", "path"], "properties": {"method": {"type": "string", "enum": ["GET", "POST", "PATCH", "PUT"]}, "path": {"type": "string"}, "body": {"type": "object"}}}),
    ]

@mcp.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    request_id = str(uuid.uuid4())
    log.log("tool_call_start", request_id=request_id, tool=name)
    try:
        if name == "list_emails": result = await tool_list_emails(arguments)
        elif name == "send_email": result = await tool_send_email(arguments)
        elif name == "hubspot_request": result = await tool_hubspot_universal_proxy(arguments)
        else: result = [TextContent(type="text", text=f"Unknown tool: {name}")]
        log.log("tool_call_success", request_id=request_id)
        return result
    except Exception as e:
        log.log("tool_call_error", request_id=request_id, error=str(e))
        return [TextContent(type="text", text=f"Error: {str(e)}")]

# ── App Handlers ─────────────────────────────────────────────────────────────

sse_transport = SseServerTransport("/messages")

async def handle_sse(request: Request):
    auth_header = request.headers.get("Authorization")
    if auth_header != f"Bearer {MCP_ACCESS_TOKEN}":
        log.log("unauthorized_access", ip=request.client.host)
        return Response("Unauthorized", status_code=401)

    # FIXED: Unpacking the streams into a readable and writeable stream
    async with sse_transport.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
        log.log("sse_connection_established", ip=request.client.host)
        
        # FIXED: Passing individual streams to mcp.run
        await mcp.run(
            read_stream, 
            write_stream, 
            mcp.create_initialization_options()
        )

async def landing(request: Request):
    base_path = os.path.dirname(__file__)
    try:
        with open(os.path.join(base_path, "templates", "landing.html"), "r") as f:
            return HTMLResponse(f.read())
    except:
        return HTMLResponse("<h1>Server Online</h1>")

# ── App Definition (Routes at the Bottom) ─────────────────────────────────────

app = Starlette(
    routes=[
        Route("/", landing),
        Route("/health", lambda r: JSONResponse({"status": "ok"})),
        Route("/sse", handle_sse, methods=["GET", "POST"]), 
        Route("/messages", endpoint=sse_transport.handle_post_message, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory="static"), name="static"),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

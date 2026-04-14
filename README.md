# AgentMail + HubSpot MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets AI agents read and send email via **AgentMail** and manage contacts and deals in **HubSpot CRM** — deployed as a public HTTP server so it works from anywhere.

## Tools

| Tool | What it does |
|---|---|
| `list_emails` | Fetch recent emails from your AgentMail inbox |
| `send_email` | Send an email from your AgentMail inbox |
| `create_hubspot_contact` | Create a contact in HubSpot CRM |
| `create_hubspot_deal` | Create a deal in HubSpot CRM |

---

## Deploy to Render (free tier)

### 1. Push this repo to GitHub

```bash
git init
git add .
git commit -m "Initial MCP server"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### 2. Create a Render account & new Web Service

1. Go to [render.com](https://render.com) and sign up (free).
2. Click **New → Web Service** and connect your GitHub repo.
3. Render will auto-detect `render.yaml` — confirm the settings.

### 3. Add your environment variables in Render

In the Render dashboard under **Environment**, add:

| Key | Where to find it |
|---|---|
| `AGENTMAIL_API_KEY` | [AgentMail Console](https://console.agentmail.to) → API Keys |
| `AGENTMAIL_INBOX_ID` | Your inbox address, e.g. `hello@agentmail.to` |
| `HUBSPOT_ACCESS_TOKEN` | HubSpot → Settings → Integrations → Private Apps → Create app → copy token |

### 4. Deploy

Click **Deploy**. After ~2 minutes your server is live at:

```
https://agentmail-hubspot-mcp.onrender.com
```

The landing page at `/` is what you list on job applications. The MCP endpoint is at `/sse`.

---

## Connect to Claude Desktop

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agentmail-hubspot": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://YOUR-APP-NAME.onrender.com/sse"
      ]
    }
  }
}
```

Config file location:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

---

## Run locally (for development)

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export AGENTMAIL_API_KEY=am_...
export AGENTMAIL_INBOX_ID=hello@agentmail.to
export HUBSPOT_ACCESS_TOKEN=pat-...

# Start the server
python server.py
```

Server runs at `http://localhost:8000`. MCP endpoint: `http://localhost:8000/sse`.

---

## Getting your API keys

### AgentMail
1. Sign up at [agentmail.to](https://agentmail.to)
2. Go to the [Console](https://console.agentmail.to)
3. Create an inbox and copy your API key

### HubSpot
1. In HubSpot, go to **Settings → Integrations → Private Apps**
2. Click **Create a private app**
3. Under **Scopes**, enable:
   - `crm.objects.contacts.write`
   - `crm.objects.deals.write`
   - `crm.objects.contacts.read`
4. Click **Create app** and copy the access token

# Company Documents MCP Server

This is a containerized Model Context Protocol (MCP) server designed to connect with Mistral agents. It manages a local SQLite database that stores company document metadata and URLs (e.g., Azure Blob Storage), and can automatically generate summaries using the Mistral OCR and Chat Completions API.

## Requirements
- Docker (for containerized deployment)
- Python 3.12 (for local development)
- A Mistral API Key (for document summarization)

## How to Run (with Docker)

This is the recommended way to run the project.

**1. Build the Docker Image:**
```bash
docker build -t company_doc_mcp_server .
```

**2. Configure your Environment Variables:**
Open the `.env` file located in the project directory and insert your actual `MISTRAL_API_KEY`:
```env
MISTRAL_API_KEY=your_actual_key_here
MCP_TRANSPORT=sse
```

**3. Run the Container:**
Pass the `.env` file to Docker using the `--env-file` flag.

*Option A: Standard stdio (for local agents)*
```bash
docker run -d \
  --name mcp_server \
  -v ~/mcp_data:/data \
  --env-file .env \
  company_doc_mcp_server
```

*Option B: Server-Sent Events (SSE) (for remote HTTP connections, e.g., remote Mistral instances)*
```bash
docker run -d \
  --name mcp_server \
  -v ~/mcp_data:/data \
  -p 8000:8000 \
  --env-file .env \
  company_doc_mcp_server
```

*(Note: The `-v ~/mcp_data:/data` flag mounts a persistent folder on your host machine to store the SQLite database so it isn't lost when the container stops).*

## How to Run (Local Development / Without Docker)

If you just want to run it via standard Python on your machine:

**1. Create a virtual environment and install dependencies:**
```bash
python -m venv venv
# On Windows: venv\Scripts\activate
# On Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
```

**2. Set your API Key:**
```bash
# On Windows PowerShell
$env:MISTRAL_API_KEY="your_mistral_api_key_here"

# On Mac/Linux
export MISTRAL_API_KEY="your_mistral_api_key_here"
```

**3. Run the Server:**
```bash
python src/server.py
```
*(Optionally set `$env:MCP_TRANSPORT="sse"` before running if you want HTTP instead of stdio).*

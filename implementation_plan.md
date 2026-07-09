# Railway Deployment Plan

To host this MCP Server on [Railway.app](https://railway.app), we need to make a few small configuration changes so it complies with Railway's container architecture. 

Railway automatically builds from `Dockerfile`s, but we need to ensure the server dynamically binds to the correct port and that your SQLite database data isn't erased every time you deploy an update.

## Proposed Changes

### 1. Dynamic Port Binding
Railway dynamically assigns a public port during deployment and injects it via the `PORT` environment variable. Currently, the FastMCP server hardcodes its port (defaulting to 8000). 
- **Change**: I will update `src/server.py` to read `os.environ.get("PORT", 8000)` and pass it into the FastMCP settings.

### 2. Persistent Storage Configuration
Railway's container filesystem is ephemeral, meaning any data saved to the disk (like our `mcp_database.sqlite`) will be completely wiped whenever the container restarts or you deploy new code.
- **Solution**: We will continue using `/data/mcp_database.sqlite` (as defined in our Dockerfile). However, **you must create a Volume in Railway and mount it to `/data`** to ensure your companies and documents persist across deployments.

## Required Actions

> [!IMPORTANT]
> Once I make the code changes and you push them to GitHub/Railway, you **must** do the following in the Railway Dashboard:
> 1. Go to your service's **Variables** tab and add your `MISTRAL_API_KEY` and set `MCP_TRANSPORT` to `sse`.
> 2. Go to your service's **Settings > Volumes** and add a new Volume. Set the Mount Path to `/data`.

Are you ready for me to update the server code for dynamic port binding?

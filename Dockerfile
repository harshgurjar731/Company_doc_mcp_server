# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the current directory contents into the container at /app
COPY src/ ./src/

# Set environment variable for the database path so it points to the volume
ENV DB_PATH=/data/mcp_database.sqlite

# Make port 8000 available to the world outside this container
EXPOSE 8000

# Run server.py when the container launches. 
# Using stdio transport is default for FastMCP, but since it's a container,
# stdio will be mapped to the container's stdin/stdout. 
# If SSE (Server-Sent Events) over HTTP is required by the Mistral agents, FastMCP can be run differently.
# `mcp.run(transport='sse')` runs an SSE server, but the default FastMCP.run() automatically picks stdio. 
# Let's use stdio by default, or run the FastMCP SSE server using CLI. 
# We'll use the CLI provided by `mcp` if we want, but since it's python, just running the script works for stdio.
# Actually, FastMCP default `run()` in the script runs stdio. 
CMD ["python", "src/server.py"]

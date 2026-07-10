from mcp.server.fastmcp import FastMCP
from db import init_db, add_company, add_document, get_company_document_summaries, get_company_documents, delete_company, delete_document, update_company, update_document_metadata, get_document, list_all_companies, get_processing_status, add_company_details, get_company_details
import json
import os
import time
import threading
import tempfile
from datetime import datetime, timedelta
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions

# Initialize the database and tables (will use DB_PATH from env or default)
init_db()

import concurrent.futures

# Initialize FastMCP Server
from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP(
    "Company Documents Server",
    # Disable DNS rebinding protection to allow Railway's dynamic Host headers
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)

def _extract_text_with_fallback(client, document_url: str) -> str:
    """Try to extract text quickly using fitz (PyMuPDF), fallback to Mistral OCR on first 15 pages."""
    import requests
    import fitz  # PyMuPDF
    
    extracted_text = ""
    # 1. Try Native PDF Extraction
    try:
        response = requests.get(document_url, timeout=30)
        response.raise_for_status()
        
        # Check if it's a valid PDF stream by trying to open it
        with fitz.open(stream=response.content, filetype="pdf") as doc:
            for i in range(min(15, len(doc))):
                extracted_text += doc[i].get_text()
                
            extracted_text = extracted_text.strip()
    except Exception as e:
        # Fails if it's not a valid PDF or not readable
        print(f"Native extraction failed for {document_url[:30]}: {e}. Falling back to OCR.")
        extracted_text = ""

    # 2. Fallback to Mistral OCR if empty
    if not extracted_text:
        print(f"Using Mistral OCR fallback (15 pages limit) for {document_url[:30]}...")
        ocr_response = client.ocr.process(
            model="mistral-ocr-latest",
            document={
                "type": "document_url",
                "document_url": document_url
            },
            pages=[i for i in range(15)]
        )
        extracted_text = "\\n\\n".join(
            getattr(page, "markdown", "") or "" 
            for page in getattr(ocr_response, "pages", [])
        )

    if not extracted_text.strip():
        return "No text could be extracted from the document."
        
    return extracted_text

def _process_single_document(client, company_name: str, document_name: str, document_url: str):
    """Worker function to process a single document."""
    try:
        extracted_text = _extract_text_with_fallback(client, document_url)

        # Summarize using Mistral Chat Complete
        chat_response = client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {
                    "role": "user",
                    "content": f"Please provide a concise summary of the following document:\\n\\n{extracted_text[:30000]}"
                }
            ]
        )
        summary = chat_response.choices[0].message.content

        # Save to database
        add_document(company_name, document_name, document_url, summary)
        return document_name, True, "Success"
    except Exception as e:
        return document_name, False, str(e)

def _process_company_documents_bg(mistral_key: str, company_name: str, blob_jobs: list):
    """Background thread to process documents in parallel using ThreadPoolExecutor."""
    try:
        from mistralai.client import Mistral
        client = Mistral(api_key=mistral_key, timeout_ms=1200000)
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_doc = {
                executor.submit(_process_single_document, client, company_name, name, url): name 
                for name, url in blob_jobs
            }
            
            for future in concurrent.futures.as_completed(future_to_doc, timeout=600):
                doc_name = future_to_doc[future]
                try:
                    _, success, err_msg = future.result()
                    if not success:
                        # Re-update the placeholder with the error
                        add_document(company_name, doc_name, "", f"Failed: {err_msg}")
                except Exception as exc:
                    add_document(company_name, doc_name, "", f"Failed: {str(exc)}")
    except Exception as e:
        print(f"Background processing error for {company_name}: {e}")

@mcp.tool()
def insert_company(company_name: str, container_name: str) -> str:
    """
    Insert a company and automatically fetch and process all documents from its Azure Blob Storage container in parallel.
    """
    try:
        from mistralai.client import Mistral
        
        azure_conn_str = os.environ.get("AZURE_CONNECTION_STRING")
        if not azure_conn_str or azure_conn_str == "your_azure_connection_string_here":
            return "Error: AZURE_CONNECTION_STRING is not configured."
            
        mistral_key = os.environ.get("MISTRAL_API_KEY")
        if not mistral_key:
            return "Error: MISTRAL_API_KEY is not configured."

        # Connect to Azure
        blob_service_client = BlobServiceClient.from_connection_string(azure_conn_str)
        container_client = blob_service_client.get_container_client(container_name)
        
        if not container_client.exists():
            return f"Error: Azure container '{container_name}' does not exist."

        # Add the company to the DB
        added = add_company(company_name, container_name)
        if not added:
            return f"Error: Company '{company_name}' already exists."

        # List blobs and generate SAS URLs
        blob_jobs = []
        for blob in container_client.list_blobs():
            blob_client = container_client.get_blob_client(blob.name)
            
            sas_token = generate_blob_sas(
                account_name=blob_service_client.account_name,
                container_name=container_name,
                blob_name=blob.name,
                account_key=blob_service_client.credential.account_key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.utcnow() + timedelta(days=7)
            )
            sas_url = f"{blob_client.url}?{sas_token}"
            blob_jobs.append((blob.name, sas_url))
            
            # Initial placeholder
            add_document(company_name, blob.name, sas_url, "Processing OCR...")

        if not blob_jobs:
            return f"Success: Company '{company_name}' added, but no documents found in container."

        # Process parallel jobs in background
        import threading
        bg_thread = threading.Thread(
            target=_process_company_documents_bg, 
            args=(mistral_key, company_name, blob_jobs),
            daemon=True
        )
        bg_thread.start()

        return f"Success: Company '{company_name}' added. Processing {len(blob_jobs)} documents in the background."
        
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def insert_document(company_name: str, document_name: str, document_url: str) -> str:
    """Insert a document record for a company. The server will automatically generate its summary using Mistral OCR."""
    try:
        import os
        from mistralai.client import Mistral
        
        api_key = os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            return "Error: MISTRAL_API_KEY environment variable is required to generate summaries."
            
        client = Mistral(api_key=api_key, timeout_ms=1200000)
        
        extracted_text = _extract_text_with_fallback(client, document_url)

        # 2. Summarize using Mistral Chat Complete
        chat_response = client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {
                    "role": "user",
                    "content": f"Please provide a concise summary of the following document:\\n\\n{extracted_text[:30000]}"
                }
            ]
        )
        summary = chat_response.choices[0].message.content

        # 3. Save to database
        add_document(company_name, document_name, document_url, summary)
        return f"Successfully added/updated document '{document_name}' for company '{company_name}'. Summary generated successfully."
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def retrieve_company_document_summaries(company_name: str) -> str:
    """Get just the document summaries for a particular company."""
    try:
        summaries = get_company_document_summaries(company_name)
        if not summaries:
            return f"No document summaries found for company '{company_name}'."
        return json.dumps(summaries, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def retrieve_company_documents(company_name: str) -> str:
    """Get all documents for a company, returning summary and URL in a proper JSON object."""
    try:
        docs = get_company_documents(company_name)
        if not docs:
            return f"No documents found for company '{company_name}'."
        # Return proper JSON object formatted as requested
        return json.dumps({"company": company_name, "documents": docs}, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def list_companies() -> str:
    """List all companies and their blob URLs currently in the database."""
    try:
        companies = list_all_companies()
        return json.dumps({"companies": companies}, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def check_company_processing_status(company_name: str) -> str:
    """Check the real-time processing status of all documents for a company."""
    try:
        status = get_processing_status(company_name)
        return json.dumps(status, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def remove_company(company_name: str) -> str:
    """Delete a company and ALL of its associated documents."""
    try:
        deleted = delete_company(company_name)
        if deleted:
            return f"Successfully deleted company '{company_name}' and its documents."
        return f"Company '{company_name}' not found."
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def remove_document(company_name: str, document_name: str) -> str:
    """Delete a specific document from a company."""
    try:
        deleted = delete_document(company_name, document_name)
        if deleted:
            return f"Successfully deleted document '{document_name}' from company '{company_name}'."
        return f"Document '{document_name}' not found in company '{company_name}'."
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def modify_company(old_name: str, new_name: str, new_blob_url: str) -> str:
    """Update a company's name and/or its Azure Blob Storage container URL."""
    try:
        updated = update_company(old_name, new_name, new_blob_url)
        if updated:
            return f"Successfully updated company '{old_name}' to '{new_name}' with new URL."
        return f"Company '{old_name}' not found."
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def modify_document(company_name: str, old_document_name: str, new_document_name: str, new_document_url: str) -> str:
    """Update a document's details. The server will automatically generate a new summary from the new URL."""
    try:
        doc = get_document(company_name, old_document_name)
        if not doc:
            return f"Document '{old_document_name}' not found in company '{company_name}'."
            
        import os
        from mistralai.client import Mistral
        
        api_key = os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            return "Error: MISTRAL_API_KEY environment variable is required to generate summaries."
            
        client = Mistral(api_key=api_key, timeout_ms=1200000)
        
        extracted_text = _extract_text_with_fallback(client, new_document_url)

        # 2. Summarize using Mistral Chat Complete
        chat_response = client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {
                    "role": "user",
                    "content": f"Please provide a concise summary of the following document:\\n\\n{extracted_text[:30000]}"
                }
            ]
        )
        new_summary = chat_response.choices[0].message.content

        # 3. Update database
        updated = update_document_metadata(company_name, old_document_name, new_document_name, new_document_url, new_summary)
        if updated:
            return f"Successfully updated document '{old_document_name}' to '{new_document_name}' and generated a new summary."
        return f"Failed to update document '{old_document_name}'."
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def insert_company_details(company_name: str, industry: str, geography: str, segment: str, kyc_status: str) -> str:
    """Insert or update company details like industry, geography, segment, and kyc_status."""
    try:
        add_company_details(company_name, industry, geography, segment, kyc_status)
        return f"Successfully updated details for company '{company_name}'."
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def retrieve_company_details(company_name: str) -> str:
    """Retrieve details like industry, geography, segment, and kyc_status for a specific company."""
    try:
        details = get_company_details(company_name)
        if not details:
            return f"No details found for company '{company_name}'."
        return json.dumps(details, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"


if __name__ == "__main__":
    import os
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport.lower() == "sse":
        import uvicorn
        from starlette.middleware.cors import CORSMiddleware
        
        # Get the underlying Starlette app from FastMCP
        app = mcp.sse_app()
        
        # Add CORS middleware to allow cross-origin requests from the MCP Inspector
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        # Configure FastMCP to bind to 0.0.0.0 and dynamic PORT for Docker/Railway networking
        port = int(os.environ.get("PORT", 8000))
        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        mcp.run(transport="stdio")

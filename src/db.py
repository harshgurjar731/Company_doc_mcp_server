import sqlite3
import os
from contextlib import contextmanager

# The database file path will be configured to point to a volume-mounted directory
DB_PATH = os.environ.get("DB_PATH", "/data/mcp_database.sqlite")

def init_db():
    """Initialize the database and create tables if they don't exist."""
    # Ensure the directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Create companies table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                blob_container_url TEXT NOT NULL
            )
        ''')
        
        # Create documents table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                document_name TEXT NOT NULL,
                document_url TEXT NOT NULL,
                summary TEXT,
                FOREIGN KEY (company_id) REFERENCES companies (id),
                UNIQUE(company_id, document_name)
            )
        ''')

        # Create batches table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS batches (
                batch_id TEXT PRIMARY KEY,
                company_name TEXT NOT NULL,
                status TEXT NOT NULL
            )
        ''')
        
        conn.commit()

@contextmanager
def get_db_connection():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # This enables dictionary-like access to rows
    try:
        yield conn
    finally:
        conn.close()

def add_company(name: str, blob_container_url: str) -> int:
    """Add a new company or update an existing one. Returns company ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO companies (name, blob_container_url)
            VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET blob_container_url=excluded.blob_container_url
        ''', (name, blob_container_url))
        conn.commit()
        
        # Get the ID of the company
        cursor.execute('SELECT id FROM companies WHERE name = ?', (name,))
        result = cursor.fetchone()
        return result['id']

def add_document(company_name: str, document_name: str, document_url: str, summary: str):
    """Add a document for a company."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Find company ID
        cursor.execute('SELECT id FROM companies WHERE name = ?', (company_name,))
        company = cursor.fetchone()
        if not company:
            raise ValueError(f"Company '{company_name}' not found. Please add the company first.")
            
        company_id = company['id']
        
        cursor.execute('''
            INSERT INTO documents (company_id, document_name, document_url, summary)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(company_id, document_name) DO UPDATE SET 
                document_url=excluded.document_url,
                summary=excluded.summary
        ''', (company_id, document_name, document_url, summary))
        conn.commit()

def get_company_document_summaries(company_name: str) -> list[dict]:
    """Get just the summaries for all documents of a specific company."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT d.document_name, d.summary
            FROM documents d
            JOIN companies c ON d.company_id = c.id
            WHERE c.name = ?
        ''', (company_name,))
        return [dict(row) for row in cursor.fetchall()]

def get_company_documents(company_name: str) -> list[dict]:
    """Get all document details (name, url, summary) for a specific company."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT d.document_name, d.document_url, d.summary
            FROM documents d
            JOIN companies c ON d.company_id = c.id
            WHERE c.name = ?
        ''', (company_name,))
        return [dict(row) for row in cursor.fetchall()]

def delete_company(company_name: str) -> bool:
    """Delete a company and all of its associated documents. Returns True if deleted."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM companies WHERE name = ?', (company_name,))
        company = cursor.fetchone()
        if not company:
            return False
            
        # Delete documents first to respect foreign key constraint
        cursor.execute('DELETE FROM documents WHERE company_id = ?', (company['id'],))
        cursor.execute('DELETE FROM companies WHERE id = ?', (company['id'],))
        conn.commit()
        return True

def delete_document(company_name: str, document_name: str) -> bool:
    """Delete a specific document. Returns True if deleted."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM companies WHERE name = ?', (company_name,))
        company = cursor.fetchone()
        if not company:
            return False
            
        cursor.execute('DELETE FROM documents WHERE company_id = ? AND document_name = ?', (company['id'], document_name))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted

def update_company(old_name: str, new_name: str, new_blob_url: str) -> bool:
    """Update a company's name and blob URL. Returns True if updated."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE companies SET name = ?, blob_container_url = ? WHERE name = ?', (new_name, new_blob_url, old_name))
        updated = cursor.rowcount > 0
        conn.commit()
        return updated

def update_document_metadata(company_name: str, old_document_name: str, new_document_name: str, new_document_url: str, new_summary: str) -> bool:
    """Update a document's details. Returns True if updated."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM companies WHERE name = ?', (company_name,))
        company = cursor.fetchone()
        if not company:
            return False
            
        cursor.execute('''
            UPDATE documents 
            SET document_name = ?, document_url = ?, summary = ?
            WHERE company_id = ? AND document_name = ?
        ''', (new_document_name, new_document_url, new_summary, company['id'], old_document_name))
        
        updated = cursor.rowcount > 0
        conn.commit()
        return updated

def get_document(company_name: str, document_name: str) -> dict | None:
    """Get a specific document."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT d.document_name, d.document_url, d.summary
            FROM documents d
            JOIN companies c ON d.company_id = c.id
            WHERE c.name = ? AND d.document_name = ?
        ''', (company_name, document_name))
        row = cursor.fetchone()
        return dict(row) if row else None

def list_all_companies() -> list[dict]:
    """List all companies and their blob URLs."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT name, blob_container_url FROM companies')
        return [dict(row) for row in cursor.fetchall()]

def get_processing_status(company_name: str) -> dict:
    """Check how many documents are still processing versus completed/failed."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM companies WHERE name = ?', (company_name,))
        company = cursor.fetchone()
        if not company:
            return {"error": f"Company '{company_name}' not found."}
            
        cursor.execute('SELECT summary FROM documents WHERE company_id = ?', (company['id'],))
        docs = cursor.fetchall()
        
        total = len(docs)
        processing = sum(1 for d in docs if d['summary'] == "Processing OCR...")
        failed = sum(1 for d in docs if str(d['summary']).startswith("Failed:"))
        completed = total - processing - failed
        
        return {
            "company": company_name,
            "total_documents": total,
            "completed": completed,
            "processing": processing,
            "failed": failed,
            "is_finished": processing == 0
        }

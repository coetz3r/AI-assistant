import sqlite3
import json
import os
import re
from datetime import datetime
import time

class MemoryManager:
    def __init__(self, db_path="memory.db"):
        # Anchor relative paths to this file's own directory, not the
        # process's cwd - otherwise launching the server from a
        # different working directory (systemd, cron, a launch script)
        # silently opens/creates a different DB with no error at all.
        if not os.path.isabs(db_path):
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), db_path)
        self.db_path = db_path
        self._init_db()
        self._log_status()

    def _log_status(self):
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0]
        print(f"[MEMORY] Using DB at {self.db_path} ({count} active facts)")

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    memory_type TEXT, -- 'user_stated', 'ai_generated', 'system_derived'
                    importance INTEGER DEFAULT 3,
                    confidence REAL DEFAULT 1.0,
                    access_count INTEGER DEFAULT 0,
                    last_accessed DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'active' -- 'active', 'decayed', 'obsolete'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON memories(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_importance ON memories(importance)")

    def get_relevant_memories(self, query_text, limit=10):
        """
        Simple keyword-based recall for now. Can be upgraded to 
        embeddings later if needed.
        """
        start_time = time.time()
        # Clean query for basic keyword matching - strip punctuation so
        # "bicycle?" still matches a stored "bicycle"
        raw_words = re.findall(r"[a-zA-Z']+", query_text.lower())
        words = [w for w in raw_words if len(w) > 3]
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if not words:
                # Fallback to most important/recent
                cursor = conn.execute("""
                    SELECT * FROM memories 
                    WHERE status = 'active' 
                    ORDER BY importance DESC, created_at DESC LIMIT ?
                """, (limit,))
            else:
                # Simple rank: match words + importance
                query_parts = " OR ".join(["content LIKE ?" for _ in words])
                params = [f"%{w}%" for w in words] + [limit]
                cursor = conn.execute(f"""
                    SELECT * FROM memories 
                    WHERE status = 'active' AND ({query_parts})
                    ORDER BY importance DESC, last_accessed DESC LIMIT ?
                """, params)
            
            results = cursor.fetchall()
            
            # Update access counts
            ids = [row['id'] for row in results]
            if ids:
                conn.execute(f"""
                    UPDATE memories SET 
                    access_count = access_count + 1,
                    last_accessed = CURRENT_TIMESTAMP
                    WHERE id IN ({','.join(['?']*len(ids))})
                """, ids)
                
        duration = time.time() - start_time
        return [dict(r) for r in results], duration

    def upsert_fact(self, content, m_type, importance, confidence):
        start_time = time.time()
        with sqlite3.connect(self.db_path) as conn:
            # Check for near-duplicates (simple subset match)
            cursor = conn.execute("SELECT id, content FROM memories WHERE status = 'active'")
            existing = cursor.fetchall()
            
            for row_id, old_content in existing:
                if content.lower() in old_content.lower() or old_content.lower() in content.lower():
                    # Update existing instead of insert
                    conn.execute("""
                        UPDATE memories SET 
                        importance = MAX(importance, ?),
                        confidence = (confidence + ?) / 2.0,
                        updated_at = CURRENT_TIMESTAMP,
                        status = 'active'
                        WHERE id = ?
                    """, (importance, confidence, row_id))
                    return "updated", time.time() - start_time

            # Insert new
            conn.execute("""
                INSERT INTO memories (content, memory_type, importance, confidence, last_accessed)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (content, m_type, importance, confidence))
            
        return "inserted", time.time() - start_time

    def decay_memories(self):
        """Mark low-access, old memories as decayed."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE memories SET status = 'decayed'
                WHERE status = 'active' 
                AND importance < 5 
                AND access_count < 2
                AND created_at < datetime('now', '-7 days')
            """)
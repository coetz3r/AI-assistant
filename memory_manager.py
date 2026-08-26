import sqlite3
import json
from datetime import datetime
import time

class MemoryManager:
    def __init__(self, db_path="memory.db"):
        self.db_path = db_path
        self._init_db()

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
        # Clean query for basic keyword matching
        words = [w.lower() for w in query_text.split() if len(w) > 3]
        
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
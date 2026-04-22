"""Migration script to add scheduling fields to generated_ideas table.

Run this if you have an existing database and need to add the new columns.
For new databases, init_db() will create them automatically.
"""

from sqlalchemy import text
from .db import get_engine


def migrate() -> None:
    """Add scheduling fields to generated_ideas table."""
    engine = get_engine()
    with engine.connect() as conn:
        # Check if columns exist
        result = conn.execute(text(
            "SELECT name FROM pragma_table_info('generated_ideas')"
        ))
        existing_columns = {row[0] for row in result}
        
        # Add scheduled_at if not exists
        if "scheduled_at" not in existing_columns:
            conn.execute(text(
                "ALTER TABLE generated_ideas ADD COLUMN scheduled_at TIMESTAMP"
            ))
            print("Added scheduled_at column")
        
        # Add posted_at if not exists
        if "posted_at" not in existing_columns:
            conn.execute(text(
                "ALTER TABLE generated_ideas ADD COLUMN posted_at TIMESTAMP"
            ))
            print("Added posted_at column")
        
        # Add thread_id if not exists
        if "thread_id" not in existing_columns:
            conn.execute(text(
                "ALTER TABLE generated_ideas ADD COLUMN thread_id VARCHAR(64)"
            ))
            print("Added thread_id column")
        
        # Add error_message if not exists
        if "error_message" not in existing_columns:
            conn.execute(text(
                "ALTER TABLE generated_ideas ADD COLUMN error_message TEXT"
            ))
            print("Added error_message column")
        
        conn.commit()
        print("Migration complete")


if __name__ == "__main__":
    migrate()

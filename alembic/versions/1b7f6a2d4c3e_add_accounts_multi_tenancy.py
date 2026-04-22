"""Add shared-database account tenancy

Revision ID: 1b7f6a2d4c3e
Revises: 6ef949e6973f
Create Date: 2026-04-15 11:00:00.000000

"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "1b7f6a2d4c3e"
down_revision: Union[str, Sequence[str], None] = "6ef949e6973f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_ACCOUNT_ID = 1
OWNED_TABLES = [
    "profiles",
    "runs",
    "my_posts",
    "my_replies",
    "my_post_insights",
    "my_account_insights",
    "topics",
    "post_topics",
    "affinity_creators",
    "affinity_posts",
    "recommendations",
    "public_perceptions",
    "algorithm_inferences",
    "you_profiles",
    "noteworthy_posts",
    "experiments",
    "experiment_post_classifications",
    "experiment_verdicts",
    "recommendation_outcomes",
    "lead_sources",
    "leads",
    "lead_search_logs",
    "lead_scores",
    "reply_templates",
    "lead_replies",
    "content_patterns",
    "generated_ideas",
    "pattern_performances",
]


def _account_fk_name(table_name: str) -> str:
    return f"fk_{table_name}_account_id_accounts"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("threads_access_token", sa.Text(), nullable=True),
        sa.Column("threads_user_id", sa.String(length=64), nullable=True),
        sa.Column("threads_handle", sa.String(length=128), nullable=True),
        sa.Column("enabled_capabilities", sa.JSON(), nullable=False),
        sa.Column("soft_caps", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    account_table = sa.table(
        "accounts",
        sa.column("id", sa.Integer()),
        sa.column("slug", sa.String()),
        sa.column("name", sa.String()),
        sa.column("threads_access_token", sa.Text()),
        sa.column("threads_user_id", sa.String()),
        sa.column("threads_handle", sa.String()),
        sa.column("enabled_capabilities", sa.JSON()),
        sa.column("soft_caps", sa.JSON()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    now = datetime.now(timezone.utc)
    op.bulk_insert(
        account_table,
        [
            {
                "id": DEFAULT_ACCOUNT_ID,
                "slug": "default",
                "name": "Default Account",
                "threads_access_token": None,
                "threads_user_id": None,
                "threads_handle": None,
                "enabled_capabilities": [],
                "soft_caps": {},
                "created_at": now,
                "updated_at": now,
            }
        ],
    )

    for table_name in OWNED_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(sa.Column("account_id", sa.Integer(), nullable=True))

    for table_name in OWNED_TABLES:
        op.execute(
            sa.text(
                f"UPDATE {table_name} SET account_id = :account_id WHERE account_id IS NULL"
            ).bindparams(account_id=DEFAULT_ACCOUNT_ID)
        )

    for table_name in OWNED_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column("account_id", existing_type=sa.Integer(), nullable=False)
            batch_op.create_foreign_key(
                _account_fk_name(table_name),
                "accounts",
                ["account_id"],
                ["id"],
            )


def downgrade() -> None:
    """Downgrade schema."""
    for table_name in reversed(OWNED_TABLES):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_constraint(_account_fk_name(table_name), type_="foreignkey")
            batch_op.drop_column("account_id")

    op.drop_table("accounts")

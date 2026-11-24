"""Join independent session-compaction and user-memory migration histories.

Revision ID: 20261124_0008
Revises: 20261124_0006, 20261124_0007

No DDL here: both parent branches must be upgraded before this single head.
"""

revision = "20261124_0008"
down_revision = ("20261124_0006", "20261124_0007")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

"""
✅ One-time migration + backfill script.

Ye script 2 kaam karta hai:
1. Agar `tasks.points_per_review` column DB me nahi hai, to add karta hai
   (SQLite aur Postgres dono ke liye safe).
2. Existing (purane) tasks jinka `points_per_review` abhi 0/NULL hai, unke
   liye apni campaign se points copy karke backfill karta hai — taki agar
   koi campaign baad me delete ho jaye, purane tasks ke points bhi safe
   rahein (naya code sirf naye tasks ke liye khud-b-khud points set karta
   hai; ye script sirf ek baar purana data theek karne ke liye hai).

Kaise chalayein:
    python migrate_points_backfill.py

Isko deploy karte waqt EK BAAR chalana hai (naya code deploy karne ke
baad, ya pehle — dono chalega, script khud check karke hi kaam karega).
Dobara chalane se bhi koi nuksan nahi (idempotent hai).
"""

import sys
from sqlalchemy import text, inspect

from app import app
from models import db, Task


def column_exists(table_name, column_name):
    inspector = inspect(db.engine)
    columns = [c['name'] for c in inspector.get_columns(table_name)]
    return column_name in columns


def add_points_column_if_missing():
    if column_exists('tasks', 'points_per_review'):
        print("✅ 'points_per_review' column already exists in 'tasks' — skip.")
        return

    print("➕ Adding 'points_per_review' column to 'tasks' table...")
    dialect = db.engine.dialect.name

    if dialect == 'sqlite':
        ddl = "ALTER TABLE tasks ADD COLUMN points_per_review INTEGER DEFAULT 0"
    else:
        # PostgreSQL and most other dialects support this syntax too
        ddl = "ALTER TABLE tasks ADD COLUMN points_per_review INTEGER DEFAULT 0"

    with db.engine.begin() as conn:
        conn.execute(text(ddl))

    print("✅ Column added.")


def backfill_existing_task_points():
    print("🔄 Backfilling points for existing tasks (jinka points_per_review 0/NULL hai)...")

    # Sirf wo tasks jinke pass abhi tak snapshot nahi hai, aur jinki
    # campaign abhi bhi exist karti hai (delete nahi hui) — unse points
    # copy kar sakte hain. Jinki campaign already delete ho chuki hai
    # (campaign_id NULL), unke liye purana data available nahi hai, wo
    # is script se recover nahi ho sakte — unhe manually handle karna
    # padega agar zaroorat ho.
    tasks_to_fix = (
        Task.query
        .filter(
            (Task.points_per_review.is_(None)) | (Task.points_per_review == 0),
            Task.campaign_id.isnot(None)
        )
        .all()
    )

    fixed_count = 0
    skipped_no_campaign = 0

    for task in tasks_to_fix:
        campaign = task.campaign
        if campaign and campaign.points_per_review:
            task.points_per_review = campaign.points_per_review
            fixed_count += 1

    db.session.commit()

    # Kitne tasks aise hain jinki campaign already delete ho chuki thi
    # (isliye unhe fix nahi kiya ja saka) — sirf jaankari ke liye count.
    orphaned_zero_point_tasks = (
        Task.query
        .filter(
            (Task.points_per_review.is_(None)) | (Task.points_per_review == 0),
            Task.campaign_id.is_(None)
        )
        .count()
    )

    print(f"✅ {fixed_count} tasks fixed (campaign se points copy kiya).")
    if orphaned_zero_point_tasks:
        print(
            f"⚠️  {orphaned_zero_point_tasks} tasks aise hain jinki campaign "
            f"pehle hi delete ho chuki thi (campaign_id NULL) — inke original "
            f"points recover nahi ho sakte, kyunki wo data wahin se pata chalta "
            f"tha jo already delete ho chuka hai. Ye tasks purani buggy state "
            f"se hain aur 0 points hi dikhayenge."
        )


if __name__ == '__main__':
    with app.app_context():
        try:
            add_points_column_if_missing()
            backfill_existing_task_points()
            print("\n🎉 Migration + backfill complete!")
        except Exception as e:
            print(f"\n❌ Migration failed: {e}")
            sys.exit(1)

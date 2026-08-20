"""Idempotent, additive migration for task lifecycle and policy-safe proof data.

Render/Hostinger runs this before Gunicorn. It creates only missing
tables/columns, then backfills derived values without deleting historical or
financial rows.
"""

from datetime import datetime, time, timedelta

from app import app, db, apply_additive_schema_migrations
from models import (
    Campaign,
    CampaignAllocationProgress,
    CampaignReviewText,
    Task,
    TaskActivityLog,
)


def _deadline_for(task, campaign):
    assigned = task.assigned_date or datetime.utcnow()
    deadline = assigned + timedelta(days=app.config['TASK_DEADLINE_DAYS'])
    if campaign and campaign.end_date:
        campaign_deadline = datetime.combine(campaign.end_date, time.max)
        deadline = min(deadline, campaign_deadline)
    return deadline


def backfill():
    campaigns = {campaign.id: campaign for campaign in Campaign.query.all()}
    prompts_by_campaign = {}
    for prompt in CampaignReviewText.query.order_by(CampaignReviewText.id).all():
        prompt.usage_count = 0
        prompt.is_used = False
        prompt.last_used_at = None
        prompts_by_campaign.setdefault(prompt.campaign_id, {}).setdefault(
            prompt.review_text, []
        ).append(prompt)

    for task in Task.query.order_by(Task.id).all():
        assigned = task.assigned_date or datetime.utcnow()
        task.allocation_month = assigned.month
        task.allocation_year = assigned.year
        task.task_kind = task.task_kind or 'InternalFeedback'
        if not task.due_date:
            task.due_date = _deadline_for(task, campaigns.get(task.campaign_id))

        matches = prompts_by_campaign.get(task.campaign_id, {}).get(task.review_text, [])
        if matches:
            prompt = matches[0]
            task.review_text_id = task.review_text_id or prompt.id
            prompt.usage_count = (prompt.usage_count or 0) + 1
            prompt.is_used = True
            if not prompt.last_used_at or assigned > prompt.last_used_at:
                prompt.last_used_at = assigned

        if not TaskActivityLog.query.filter_by(task_id=task.id).first():
            db.session.add(TaskActivityLog(
                task_id=task.id,
                user_id=task.user_id,
                campaign_id=task.campaign_id,
                action='assigned',
                to_status='Assigned',
                reason='Historical assignment backfill',
                actor_type='system',
                created_at=assigned
            ))
            if task.status != 'Assigned':
                event_time = task.submission_date or assigned
                if task.submission and task.submission.verified_date:
                    event_time = task.submission.verified_date
                db.session.add(TaskActivityLog(
                    task_id=task.id,
                    user_id=task.user_id,
                    campaign_id=task.campaign_id,
                    action=task.status.lower(),
                    from_status='Assigned',
                    to_status=task.status,
                    reason=task.cancel_reason or task.expiry_reason or 'Historical status backfill',
                    actor_type='system',
                    created_at=event_time
                ))

    for campaign in campaigns.values():
        campaign.workflow_type = campaign.workflow_type or 'InternalFeedback'
        progress = CampaignAllocationProgress.query.filter_by(campaign_id=campaign.id).first()
        if not progress:
            continue
        tasks = Task.query.filter_by(campaign_id=campaign.id).all()
        progress.total_tasks_created = len(tasks)
        progress.total_tasks_assigned = len(tasks)
        progress.total_tasks_completed = sum(task.status == 'Approved' for task in tasks)
        progress.total_tasks_expired = sum(task.status == 'Expired' for task in tasks)
        progress.total_tasks_cancelled = sum(task.status == 'Cancelled' for task in tasks)
        progress.users_count_assigned = len({task.user_id for task in tasks})
        progress.users_count_completed = len({
            task.user_id for task in tasks if task.status == 'Approved'
        })

    # A legacy campaign may contain duplicate prompt rows. If that text has
    # ever been assigned, mark every duplicate row used so it cannot be handed
    # to another user later.
    for prompt_map in prompts_by_campaign.values():
        for duplicates in prompt_map.values():
            if any((prompt.usage_count or 0) > 0 for prompt in duplicates):
                for prompt in duplicates:
                    prompt.is_used = True

    db.session.commit()


if __name__ == '__main__':
    with app.app_context():
        apply_additive_schema_migrations()
        backfill()
        print('Task lifecycle migration completed successfully.')

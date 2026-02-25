-- Onboarding metadata for assistant-first bootstrap routing.

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS onboarding_status TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS playbook JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Backward compatibility: users onboarded before this field existed should
-- continue going directly to the assistant if they already have complete
-- scheduler preferences.
UPDATE public.users
SET onboarding_status = 'completed'
WHERE onboarding_status = 'pending'
  AND wake_time IS NOT NULL
  AND bedtime IS NOT NULL
  AND timezone IS NOT NULL;

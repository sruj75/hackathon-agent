-- Add explicit RLS policy for composio_connections to satisfy security linting
-- while keeping access scoped to the authenticated owning user.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'composio_connections'
          AND policyname = 'Users can access own composio connections'
    ) THEN
        CREATE POLICY "Users can access own composio connections"
            ON public.composio_connections FOR ALL
            TO authenticated
            USING (auth.uid()::text = user_id)
            WITH CHECK (auth.uid()::text = user_id);
    END IF;
END
$$;

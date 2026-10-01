-- Write-capable connection for read-write agents: INSERT on public.follow_up_tasks ONLY.
-- Apply as the database owner. No credential is rendered here: set the role's password with apply_writer_password (from $WALNUT_DEMO_PG_WRITER_PASSWORD).
CREATE TABLE IF NOT EXISTS public.follow_up_tasks (
    task_id         BIGSERIAL     PRIMARY KEY,
    customer_id     VARCHAR(12)   NOT NULL,
    invoice_no      VARCHAR(20),
    title           VARCHAR(200)  NOT NULL,
    due_on          DATE,
    created_by      VARCHAR(120)  NOT NULL,
    agent_run_id    VARCHAR(80),
    source_pointer  TEXT,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE ROLE walnut_agent_writer LOGIN;
GRANT CONNECT ON DATABASE larkspur TO walnut_agent_writer;
GRANT USAGE ON SCHEMA public TO walnut_agent_writer;
GRANT INSERT ON public.follow_up_tasks TO walnut_agent_writer;
GRANT USAGE ON SEQUENCE public.follow_up_tasks_task_id_seq TO walnut_agent_writer;
-- The reader sees what agents wrote, so the next sync re-ingests it (closes the loop):
GRANT SELECT ON public.follow_up_tasks TO walnut_reader;

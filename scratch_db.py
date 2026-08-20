import asyncio; import asyncpg; async def main(): pool = await asyncpg.create_pool('postgresql://postgres:postgres@localhost:5432/p118_db'); rows = await pool.fetch(\
SELECT
*
FROM
workflows
WHERE
id
=
3212d442-6fa5-47fe-85d7-7de277437add
\); print('WORKFLOW', rows[0]); task_rows = await pool.fetch(\SELECT
*
FROM
workflow_tasks
WHERE
workflow_id
=
3212d442-6fa5-47fe-85d7-7de277437add
ORDER
BY
created_at\); print('TASKS:'); for r in task_rows: print(r['task_id'], r['tool'], r['status'], r['depends_on']); await pool.close(); asyncio.run(main())

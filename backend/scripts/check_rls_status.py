import psycopg

with psycopg.connect('postgresql://postgres:postgres@localhost:5432/myca') as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' ORDER BY relname;")
        for r in cur.fetchall():
            print(f"{r[0]:35} | RLS: {r[1]} | FORCE: {r[2]}")

import psycopg

with psycopg.connect('postgresql://postgres:postgres@localhost:5432/myca') as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT grantee, table_name, privilege_type 
            FROM information_schema.role_table_grants 
            WHERE grantee = 'app_service_user' 
              AND table_name IN ('government_schemes', 'scheme_eligibility_rules')
            ORDER BY table_name, privilege_type;
        """)
        rows = cur.fetchall()
        print(f"Total grants found for app_service_user on reference tables: {len(rows)}")
        for r in rows:
            print(f"Grantee: {r[0]} | Table: {r[1]:25} | Privilege: {r[2]}")

import psycopg

conn_str = "postgresql://postgres:postgres@localhost:5432/myca"
with psycopg.connect(conn_str) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id, legal_name, pan FROM organizations WHERE id = '00000000-0000-4000-a000-000000000001';")
        org = cur.fetchone()
        print("Demo Org:", org)
        if org:
            org_pan = org[2]
            # Form matching GSTIN: 27{org_pan}1ZT
            gstin = f"27{org_pan}1ZT"
            reg_id = "00000000-0000-4000-a000-000000000003"
            cur.execute("""
                INSERT INTO gst_registrations (id, org_id, gstin, legal_name, state_code, is_primary)
                VALUES (%s, %s, %s, %s, '27', true)
                ON CONFLICT (id) DO UPDATE SET gstin = EXCLUDED.gstin;
            """, (reg_id, org[0], gstin, org[1]))
            conn.commit()
            print("Successfully registered GSTIN:", gstin, "for Org:", org[1])

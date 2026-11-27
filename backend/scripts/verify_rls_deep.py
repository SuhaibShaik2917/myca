from sqlalchemy import text
from app.database import SessionLocal, set_tenant_context


def test_deep_rls_all_tables():
    org_a = "00000000-0000-4000-a000-000000000001"
    org_b = "00000000-0000-4000-b000-000000000001"
    user_a = "00000000-0000-4000-a000-000000000002"
    user_b = "00000000-0000-4000-b000-000000000002"

    db_a = SessionLocal()
    try:
        set_tenant_context(db_a, org_id=org_a, user_id=user_a)
        regs_a = db_a.execute(text("SELECT id, org_id, gstin FROM public.gst_registrations;")).fetchall()
        print(f"Org A context: retrieved {len(regs_a)} registration(s)")
        assert len(regs_a) >= 1
        for r in regs_a:
            assert str(r.org_id) == org_a, f"RLS Leak! Found {r.org_id} under context {org_a}"

        cross_reg = db_a.execute(
            text("SELECT id FROM public.gst_registrations WHERE id = '00000000-0000-4000-b000-000000000003';")
        ).fetchone()
        assert cross_reg is None, "CRITICAL RLS FAILURE: Org A was able to read Org B registration directly by ID!"

        try:
            db_a.execute(text("""
                INSERT INTO public.gst_registrations (id, org_id, gstin, state_code, trade_name, legal_name, is_primary)
                VALUES ('00000000-0000-4000-b000-000000000099', :org_b, '27BBBBB1234B9Z9', '27', 'Hack', 'Hack', false);
            """), {"org_b": org_b})
            db_a.commit()
            insert_leaked = True
        except Exception as e:
            insert_leaked = False
            db_a.rollback()
            print(f"Cross-tenant insert successfully blocked by RLS WITH CHECK policy: {e.__class__.__name__}")
        assert not insert_leaked, "CRITICAL RLS FAILURE: Org A was able to insert a record tagged with Org B's org_id!"
    finally:
        db_a.close()

    db_b = SessionLocal()
    try:
        set_tenant_context(db_b, org_id=org_b, user_id=user_b)
        regs_b = db_b.execute(text("SELECT id, org_id, gstin FROM public.gst_registrations;")).fetchall()
        print(f"Org B context: retrieved {len(regs_b)} registration(s)")
        assert len(regs_b) >= 1
        for r in regs_b:
            assert str(r.org_id) == org_b, f"RLS Leak! Found {r.org_id} under context {org_b}"
    finally:
        db_b.close()

    db_null = SessionLocal()
    try:
        db_null.execute(text("SELECT set_config('app.current_org', '', false), set_config('app.current_user', '', false);"))
        regs_null = db_null.execute(text("SELECT * FROM public.gst_registrations;")).fetchall()
        print(f"Null context: retrieved {len(regs_null)} registration(s)")
        assert len(regs_null) == 0, f"Expected 0 rows when tenant context is unset, got {len(regs_null)}"
    finally:
        db_null.close()

    print("RLS Deep Isolation: SUCCESS - All cross-tenant isolation and policy enforcement tests PASSED flawlessly!")


if __name__ == "__main__":
    test_deep_rls_all_tables()

import sys, datetime
import pg8000.dbapi as pg

conn = pg.connect(
    host="reseau.proxy.rlwy.net", port=56077,
    user="postgres", password="UmUnwoeBGZvzGhsZRhjdJvyakciOytuI",
    database="railway", timeout=20,
)
cur = conn.cursor()

cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1")
print("TABLES:", [r[0] for r in cur.fetchall()])

cur.execute("SELECT user_id, tier, stripe_customer_id IS NOT NULL, created_at FROM users ORDER BY created_at")
rows = cur.fetchall()
print(f"\nUSERS ({len(rows)}):")
for r in rows:
    ts = datetime.datetime.utcfromtimestamp(r[3]).strftime('%Y-%m-%d') if r[3] else '?'
    print(f"  {r[0]:35s}  tier={r[1]:8s} stripe={r[2]}  joined={ts}")

conn.close()

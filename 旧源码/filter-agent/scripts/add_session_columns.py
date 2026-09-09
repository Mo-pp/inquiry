import mysql.connector

c = mysql.connector.connect(
    host="127.0.0.1", port=3306, user="root",
    password="123456", database="lintratek_chat", charset="utf8mb4",
)
cur = c.cursor()
cur.execute(
    "ALTER TABLE customers "
    "ADD COLUMN agent_session_id VARCHAR(64) NULL, "
    "ADD COLUMN session_updated_at DATETIME NULL"
)
c.commit()
cur.execute("DESCRIBE customers")
for row in cur.fetchall():
    print(row)
c.close()

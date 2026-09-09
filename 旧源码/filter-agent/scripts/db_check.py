import mysql.connector

c = mysql.connector.connect(
    host="127.0.0.1", port=3306, user="root",
    password="123456", database="lintratek_chat", charset="utf8mb4",
)
cur = c.cursor()
cur.execute(
    "SELECT customer_phone, agent_session_id, session_updated_at FROM customers"
)
for row in cur.fetchall():
    print(row)
c.close()

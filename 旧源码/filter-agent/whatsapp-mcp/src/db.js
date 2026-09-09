import path from "node:path";
import { fileURLToPath } from "node:url";
import dotenv from "dotenv";

const here = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.resolve(here, "..", ".env"), quiet: true });
dotenv.config({ path: path.resolve(here, "..", "..", ".env"), quiet: true });

export function loadDbConfig() {
  const port = Number.parseInt(process.env.MYSQL_PORT || "3306", 10);
  return {
    host: process.env.MYSQL_HOST || "127.0.0.1",
    port: Number.isInteger(port) ? port : 3306,
    user: process.env.MYSQL_USER || "root",
    password: process.env.MYSQL_PASSWORD || "",
    database: process.env.MYSQL_DATABASE || "lintratek_chat",
  };
}

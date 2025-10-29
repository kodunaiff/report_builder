#!/usr/bin/env python3
import argparse, os, sys, sqlite3, csv
from pathlib import Path

import pandas as pd

# ----- DB helpers -----

def apply_pragmas(conn: sqlite3.Connection):
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")

def create_schema(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS sellers(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY,
        external_id TEXT NOT NULL,
        date TEXT NOT NULL,
        channel TEXT NOT NULL,
        seller_id INTEGER,
        status TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        delivered_at TEXT,
        FOREIGN KEY(seller_id) REFERENCES sellers(id)
    );
    CREATE TABLE IF NOT EXISTS order_items(
        order_id INTEGER NOT NULL,
        sku TEXT NOT NULL,
        qty INTEGER NOT NULL,
        revenue REAL NOT NULL,
        cost REAL NOT NULL,
        FOREIGN KEY(order_id) REFERENCES orders(id)
    );
    """)

def load_csv_sellers(conn: sqlite3.Connection, path: Path):
    with open(path, newline="", encoding="utf-8") as f, conn:
        dr = csv.DictReader(f)
        rows = [(int(r["id"]), r["name"]) for r in dr]
        conn.executemany("INSERT INTO sellers(id, name) VALUES(?,?)", rows)

def load_csv_orders(conn: sqlite3.Connection, path: Path):
    with open(path, newline="", encoding="utf-8") as f, conn:
        dr = csv.DictReader(f)
        rows = []
        for r in dr:
            delivered = r["delivered_at"].strip()
            delivered = None if delivered == "" else delivered
            seller_id = r["seller_id"].strip()
            seller_id = None if seller_id == "" else int(seller_id)
            rows.append((
                int(r["id"]),
                r["external_id"],
                r["date"],
                r["channel"],
                seller_id,
                r["status"],
                r["updated_at"],
                delivered
            ))
        conn.executemany("""
            INSERT INTO orders(id, external_id, date, channel, seller_id, status, updated_at, delivered_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, rows)

def load_csv_order_items(conn: sqlite3.Connection, path: Path):
    with open(path, newline="", encoding="utf-8") as f, conn:
        dr = csv.DictReader(f)
        rows = []
        for r in dr:
            rows.append((
                int(r["order_id"]),
                r["sku"],
                int(r["qty"]),
                float(r["revenue"]),
                float(r["cost"])
            ))
        conn.executemany("""
            INSERT INTO order_items(order_id, sku, qty, revenue, cost)
            VALUES (?,?,?,?,?)
        """, rows)

def create_indexes(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE INDEX IF NOT EXISTS idx_orders_external_id ON orders(external_id);
    CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(date);
    CREATE INDEX IF NOT EXISTS idx_orders_delivered_at ON orders(delivered_at);
    CREATE INDEX IF NOT EXISTS idx_orders_updated_at ON orders(updated_at);
    CREATE INDEX IF NOT EXISTS idx_items_order_id ON order_items(order_id);
    CREATE INDEX IF NOT EXISTS idx_orders_seller_id ON orders(seller_id);
    """)

def read_sql_export(sql_path: Path) -> str:
    with open(sql_path, "r", encoding="utf-8") as f:
        return f.read()

# ----- Report builders -----

def build_summary_df(orders_df: pd.DataFrame) -> pd.DataFrame:
    # агрегаты по channel и seller
    grp = (orders_df
           .groupby(["channel", "seller"], dropna=False)
           .agg(
               revenue_sum=("revenue", "sum"),
               cost_sum=("cost", "sum"),
               margin_sum=("margin", "sum"),
               positions_count=("sku", "count"),
               unique_orders=("order_id", "nunique"),
           )
           .reset_index()
           .sort_values(["channel","seller"])
    )
    return grp

def compute_conversions_from_orders_df(orders_df: pd.DataFrame):
    # по заказам (dedup уже применён в export.sql), берём уникальные order_id
    u = orders_df[["order_id", "status"]].drop_duplicates(subset=["order_id"])
    total = len(u)
    def cnt(statuses): return int(u["status"].isin(statuses).sum())
    delivered = cnt(["delivered"])
    shipped = cnt(["shipped", "delivered"])
    prod_started = cnt(["prod_started", "shipped", "delivered"])
    paid = cnt(["paid", "prod_started", "shipped", "delivered"])

    steps = [
        ("created", total),
        ("paid", paid),
        ("prod_started", prod_started),
        ("shipped", shipped),
        ("delivered", delivered),
    ]
    # пошаговые конверсии
    def safe_rate(a, b): return (b / a) if a else 0.0
    step_rates = [
        ("paid/created", safe_rate(total, paid)),
        ("prod_started/paid", safe_rate(paid, prod_started)),
        ("shipped/prod_started", safe_rate(prod_started, shipped)),
        ("delivered/shipped", safe_rate(shipped, delivered)),
        ("delivered/created", safe_rate(total, delivered)),
    ]
    return steps, step_rates

def write_excel(out_path: Path, orders_df: pd.DataFrame):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="xlsxwriter") as writer:
        wb = writer.book

        # Orders
        orders_df.to_excel(writer, sheet_name="Orders", index=False)
        ws_orders = writer.sheets["Orders"]
        money_fmt = wb.add_format({"num_format":"#,##0.00"})
        int_fmt = wb.add_format({"num_format":"0"})
        # Колонки: qty int, revenue/cost/margin money
        # Найдём индексы столбцов
        cols = {c:i for i,c in enumerate(orders_df.columns, start=0)}
        if "qty" in cols: ws_orders.set_column(cols["qty"], cols["qty"], 10, int_fmt)
        for c in ["revenue","cost","margin"]:
            if c in cols:
                ws_orders.set_column(cols[c], cols[c], 14, money_fmt)
        ws_orders.freeze_panes(1, 0)

        # Summary
        summary_df = build_summary_df(orders_df)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        ws_sum = writer.sheets["Summary"]
        ws_sum.freeze_panes(1, 0)

        # Dashboard
        ws_dash = wb.add_worksheet("Dashboard")
        writer.sheets["Dashboard"] = ws_dash

        # Маржа по каналам
        margin_by_channel = (orders_df.groupby("channel", dropna=False)["margin"].sum()
                             .reset_index().rename(columns={"margin":"margin_sum"}))
        ws_dash.write_row(0, 0, ["channel","margin_sum"])
        for i, row in margin_by_channel.iterrows():
            ws_dash.write(i+1, 0, row["channel"])
            ws_dash.write_number(i+1, 1, float(row["margin_sum"]))
        # Chart 1
        chart1 = wb.add_chart({"type":"column"})
        n = len(margin_by_channel)
        chart1.add_series({
            "name": "Margin by channel",
            "categories": ["Dashboard", 1, 0, n, 0],
            "values":     ["Dashboard", 1, 1, n, 1],
        })
        chart1.set_title({"name":"Маржа по каналам"})
        chart1.set_y_axis({"num_format":"#,##0.00"})
        ws_dash.insert_chart(0, 3, chart1, {"x_scale":1.2, "y_scale":1.2})

        # Конверсии
        steps, step_rates = compute_conversions_from_orders_df(orders_df)
        start_row = n + 3
        ws_dash.write_row(start_row, 0, ["stage","count"])
        for i, (stage, cnt) in enumerate(steps):
            ws_dash.write(start_row + 1 + i, 0, stage)
            ws_dash.write_number(start_row + 1 + i, 1, cnt)

        # Таблица с rate’ами (для графика)
        rate_start = start_row
        ws_dash.write_row(rate_start, 5, ["conversion_step","rate"])
        for i, (name, rate) in enumerate(step_rates):
            ws_dash.write(rate_start + 1 + i, 5, name)
            ws_dash.write_number(rate_start + 1 + i, 6, rate)

        # Chart 2 — конверсии (в процентах)
        chart2 = wb.add_chart({"type":"line"})
        m = len(step_rates)
        chart2.add_series({
            "name": "Conversion",
            "categories": ["Dashboard", rate_start+1, 5, rate_start+m, 5],
            "values":     ["Dashboard", rate_start+1, 6, rate_start+m, 6],
            "data_labels": {"value": True, "num_format":"0.0%"},
        })
        chart2.set_title({"name":"Конверсия по статусам"})
        chart2.set_y_axis({"num_format":"0%", "major_gridlines": {"visible": True}})
        ws_dash.insert_chart(start_row, 3, chart2, {"x_scale":1.2, "y_scale":1.2})

        # Checks
        ws_chk = wb.add_worksheet("Checks")
        writer.sheets["Checks"] = ws_chk
        bold = wb.add_format({"bold": True})

        row = 0
        def write_section(title, df):
            nonlocal row
            ws_chk.write(row, 0, title, bold); row += 1
            if df.empty:
                ws_chk.write(row, 0, "OK"); row += 2
                return
            df.to_excel(writer, sheet_name="Checks", startrow=row, startcol=0, index=False)
            row += len(df) + 2

        # 1) qty <= 0
        write_section("qty <= 0", orders_df.loc[orders_df["qty"] <= 0, :])

        # 2) margin < 0
        write_section("margin < 0", orders_df.loc[orders_df["margin"] < 0, :])

        # 3) отсутствует seller или channel
        missing = orders_df.loc[orders_df["seller"].isna() | orders_df["channel"].isna() | (orders_df["channel"] == ""), :]
        write_section("отсутствует seller или channel", missing)

        # 4) дубликаты external_id в результирующей выгрузке (по разным order_id)
        dup = (orders_df.groupby("external_id")["order_id"].nunique()
               .reset_index().rename(columns={"order_id":"order_id_nunique"}))
        dup = dup.loc[dup["order_id_nunique"] > 1, :]
        write_section("дубликаты external_id (ожидается 0)", dup)

    print(f"[OK] Report written to {out_path}")

# ----- Main -----

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--out", default="excel/Report.xlsx")
    ap.add_argument("--db", default=":memory:", help="путь к SQLite или :memory:")
    ap.add_argument("--data", default="data", help="папка с sellers.csv, orders.csv, order_items.csv")
    ap.add_argument("--sql", default="sql/export.sql", help="путь к SELECT для выгрузки")
    args = ap.parse_args()

    try:
        data_dir = Path(args.data)
        sql_path = Path(args.sql)
        out_path = Path(args.out)

        if not (data_dir/"sellers.csv").exists() or not (data_dir/"orders.csv").exists() or not (data_dir/"order_items.csv").exists():
            print("[ERR] CSV не найдены. Сначала сгенерируйте данные: python data/generate_data.py --email you@example.com")
            sys.exit(2)

        # Connect DB
        conn = sqlite3.connect(args.db)
        try:
            apply_pragmas(conn)
            create_schema(conn)

            print("[..] Loading CSV into SQLite...")
            load_csv_sellers(conn, data_dir/"sellers.csv")
            load_csv_orders(conn, data_dir/"orders.csv")
            load_csv_order_items(conn, data_dir/"order_items.csv")
            create_indexes(conn)
            print("[OK] Loaded and indexed")

            print("[..] Running export SQL...")
            sql = read_sql_export(sql_path)
            orders_df = pd.read_sql_query(sql, conn, params={"days": args.days})
            print(f"[OK] Exported {len(orders_df)} rows")

            # Build Excel
            print("[..] Building Excel report...")
            write_excel(out_path, orders_df)
            print("[OK] Done")
        finally:
            conn.close()

        sys.exit(0)
    except Exception as e:
        print("[ERR]", repr(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
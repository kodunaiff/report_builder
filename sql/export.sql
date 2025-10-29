WITH period_orders AS (
  SELECT *
  FROM orders
  WHERE julianday(replace(date, 'T',' ')) >= julianday(datetime('now', '-' || :days || ' days'))
),
dedup AS (
  SELECT
    o.*,
    ROW_NUMBER() OVER (
      PARTITION BY o.external_id
      ORDER BY
        CASE WHEN o.delivered_at IS NOT NULL AND o.delivered_at <> '' THEN 1 ELSE 0 END DESC,
        julianday(replace(o.delivered_at,'T',' ')) DESC,
        julianday(replace(o.updated_at,'T',' ')) DESC
    ) AS rn
  FROM period_orders o
),
best_orders AS (
  SELECT * FROM dedup WHERE rn = 1
)
SELECT
  bo.id AS order_id,
  bo.date,
  bo.channel,
  s.name AS seller,
  bo.external_id,
  oi.sku,
  oi.qty,
  oi.revenue,
  oi.cost,
  oi.revenue - oi.cost AS margin,
  bo.status
FROM best_orders bo
JOIN order_items oi ON oi.order_id = bo.id
LEFT JOIN sellers s ON s.id = bo.seller_id
ORDER BY bo.date ASC, bo.id ASC, oi.sku ASC;
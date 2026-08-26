-- Replace the table path with your Cloud Billing Detailed export table.
-- Set the UTC start and end times to match the experiment metadata.

DECLARE run_start TIMESTAMP DEFAULT TIMESTAMP('2026-08-25 15:54:58+00');
DECLARE run_end   TIMESTAMP DEFAULT TIMESTAMP('2026-08-25 16:05:30+00');

WITH billing_lines AS (
  SELECT
    LOWER(service.description) AS service_key,
    LOWER(sku.description) AS sku_key,
    service.description AS service,
    sku.description AS sku,
    cost,
    (
      SELECT COALESCE(SUM(credit.amount), 0)
      FROM UNNEST(credits) AS credit
    ) AS credits,
    currency
  FROM
    `BILLING_EXPORT_PROJECT.BILLING_DATASET.gcp_billing_export_resource_v1_BILLING_ACCOUNT_ID`
  WHERE
    project.id = 'tests101-483015'
    AND usage_start_time < run_end
    AND usage_end_time > run_start
),
classified AS (
  SELECT
    CASE
      WHEN REGEXP_CONTAINS(service_key, r'vertex ai|cloud run|cloud scheduler|cloud storage|cloud monitoring')
        THEN 'Agent'
      WHEN REGEXP_CONTAINS(service_key, r'cloud logging|cloud build|artifact registry')
        THEN 'Supporting / other'
      WHEN service_key LIKE '%compute engine%'
        OR REGEXP_CONTAINS(sku_key, r'network|data transfer|load balanc|forwarding rule|external ip')
        THEN 'Workload'
      ELSE 'Supporting / other'
    END AS cost_owner,
    CASE
      WHEN REGEXP_CONTAINS(
        service_key,
        r'cloud run|cloud storage|cloud monitoring|vertex ai|cloud scheduler'
      ) AND REGEXP_CONTAINS(
        sku_key,
        r'network|data transfer'
      )
        THEN 'Agent - network'
      WHEN service_key LIKE '%vertex ai%'
        THEN 'Agent - model'
      WHEN service_key LIKE '%cloud run%'
        THEN 'Agent - runtime'
      WHEN service_key LIKE '%cloud scheduler%'
        THEN 'Agent - scheduler'
      WHEN service_key LIKE '%cloud storage%'
        THEN 'Agent - state storage'
      WHEN service_key LIKE '%cloud monitoring%'
        THEN 'Agent - monitoring'
      WHEN service_key LIKE '%cloud logging%'
        THEN 'Supporting - logging'
      WHEN REGEXP_CONTAINS(service_key, r'cloud build|artifact registry')
        THEN 'Supporting - deployment'
      WHEN REGEXP_CONTAINS(
        sku_key,
        r'network|data transfer|load balanc|forwarding rule|external ip'
      )
        THEN 'Workload - network and load balancer'
      WHEN service_key LIKE '%compute engine%'
        THEN 'Workload - MIG compute'
      ELSE CONCAT('Other - ', service)
    END AS component,
    service,
    sku,
    cost,
    credits,
    currency
  FROM billing_lines
)
SELECT
  cost_owner,
  component,
  service,
  sku,
  ROUND(SUM(cost), 6) AS gross_cost,
  ROUND(SUM(credits), 6) AS credits,
  ROUND(SUM(cost + credits), 6) AS net_cost,
  ANY_VALUE(currency) AS currency
FROM classified
GROUP BY 1, 2, 3, 4
ORDER BY cost_owner, net_cost DESC, component, sku;

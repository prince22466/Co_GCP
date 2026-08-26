-- GKE Autopilot experiment cost breakdown.
-- Replace the billing-export table path and set the experiment's UTC window.
-- GKE cost allocation must be enabled before the experiment.

DECLARE run_start TIMESTAMP DEFAULT TIMESTAMP('2000-01-01 00:00:00+00');
DECLARE run_end   TIMESTAMP DEFAULT TIMESTAMP('2000-01-01 00:10:30+00');

WITH billing_lines AS (
  SELECT
    LOWER(service.description) AS service_key,
    LOWER(sku.description) AS sku_key,
    service.description AS service,
    sku.description AS sku,
    COALESCE(resource.name, resource.global_name, '(not itemized)') AS resource_name,
    (
      SELECT ANY_VALUE(label.value)
      FROM UNNEST(labels) AS label
      WHERE label.key = 'goog-k8s-cluster-name'
    ) AS cluster_name,
    (
      SELECT ANY_VALUE(label.value)
      FROM UNNEST(labels) AS label
      WHERE label.key = 'k8s-namespace'
    ) AS namespace,
    (
      SELECT ANY_VALUE(label.value)
      FROM UNNEST(labels) AS label
      WHERE label.key = 'k8s-workload-name'
    ) AS workload_name,
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
      WHEN service_key LIKE '%kubernetes engine%'
        AND REGEXP_CONTAINS(sku_key, r'cluster.*management|management.*cluster')
        THEN 'Management service'
      WHEN sku_key LIKE '%autopilot%'
        AND REGEXP_CONTAINS(sku_key, r'vcpu|cpu|memory|storage')
        THEN 'Workload resources'
      WHEN service_key LIKE '%compute engine%'
        OR REGEXP_CONTAINS(
          sku_key,
          r'network|data transfer|load balanc|forwarding rule|external ip'
        )
        THEN 'Workload resources'
      WHEN REGEXP_CONTAINS(
        service_key,
        r'cloud logging|cloud monitoring|cloud build|artifact registry'
      )
        THEN 'Supporting / other'
      ELSE 'Review'
    END AS cost_scope,
    CASE
      WHEN sku_key LIKE '%autopilot%'
        AND REGEXP_CONTAINS(sku_key, r'vcpu|cpu')
        THEN 'GKE workload - Autopilot Pod vCPU'
      WHEN sku_key LIKE '%autopilot%'
        AND sku_key LIKE '%memory%'
        THEN 'GKE workload - Autopilot Pod memory'
      WHEN sku_key LIKE '%autopilot%'
        AND sku_key LIKE '%storage%'
        THEN 'GKE workload - Autopilot Pod storage'
      WHEN service_key LIKE '%kubernetes engine%'
        AND REGEXP_CONTAINS(sku_key, r'cluster.*management|management.*cluster')
        THEN 'GKE management - cluster management fee'
      WHEN service_key LIKE '%kubernetes engine%'
        OR sku_key LIKE '%autopilot%'
        THEN 'GKE other - review Kubernetes Engine SKU'
      WHEN REGEXP_CONTAINS(
        sku_key,
        r'network|data transfer|load balanc|forwarding rule|external ip'
      )
        THEN 'GKE workload - network and load balancer'
      WHEN service_key LIKE '%compute engine%'
        THEN 'GKE workload - other Compute Engine'
      WHEN REGEXP_CONTAINS(service_key, r'cloud logging|cloud monitoring')
        THEN 'GKE supporting - observability'
      WHEN REGEXP_CONTAINS(service_key, r'cloud build|artifact registry')
        THEN 'GKE supporting - deployment'
      ELSE CONCAT('Other - ', service)
    END AS component,
    service,
    sku,
    resource_name,
    cluster_name,
    namespace,
    workload_name,
    cost,
    credits,
    currency
  FROM billing_lines
)
SELECT
  cost_scope,
  component,
  service,
  sku,
  resource_name,
  cluster_name,
  namespace,
  workload_name,
  ROUND(SUM(cost), 6) AS gross_cost,
  ROUND(SUM(credits), 6) AS credits,
  ROUND(SUM(cost + credits), 6) AS net_cost,
  ANY_VALUE(currency) AS currency
FROM classified
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
ORDER BY cost_scope, net_cost DESC, component, sku;

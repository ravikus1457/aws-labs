#!/usr/bin/env bash
# Exercise + evidence capture for Lab 07 (EKS + observability).
# Receives the evidence dir as $1. Reads terraform outputs from $OUTPUTS_JSON.
# Used by BOTH scripts/run-lab.sh (local) and the CI smoke job (same contract).
#
# Locally it also INSTALLS the two Helm releases (idempotent `upgrade --install`);
# in CI the deploy job installs them first and runs this with EXERCISE_INSTALL=0.
#
# Needs: aws, kubectl, helm, jq, curl.   Env knobs (all optional):
#   EXERCISE_INSTALL=1   run the helm installs (default 1)
#   EXERCISE_FAULT=1     hit /boom and watch the 5xx alert go pending/firing (default 1)
#   IMAGE_TAG=<sha>      image tag to deploy; default = newest tag in lab 06's ECR repo
#   KPS_VERSION=91.8.2   kube-prometheus-stack chart version
#   GRAFANA_ADMIN_PASSWORD   set on install; otherwise generated and stored in the evidence dir
set -euo pipefail
EVID="${1:?evidence dir required}"
OUTPUTS_JSON="${OUTPUTS_JSON:?path to terraform output -json}"
mkdir -p "$EVID"

CLUSTER="$(jq -r '.cluster_name.value' "$OUTPUTS_JSON")"
REGION="${AWS_REGION:-$(jq -r '.aws_region.value' "$OUTPUTS_JSON")}"
NODE_COUNT="$(jq -r '.node_count.value' "$OUTPUTS_JSON")"
APP_NS="$(jq -r '.app_namespace.value' "$OUTPUTS_JSON")"
APP_SA="$(jq -r '.app_service_account.value' "$OUTPUTS_JSON")"
IRSA_ROLE="$(jq -r '.app_irsa_role_arn.value' "$OUTPUTS_JSON")"
ECR_URL="$(jq -r '.ecr_repository_url.value' "$OUTPUTS_JSON")"
ECR_NAME="$(jq -r '.ecr_repository_name.value' "$OUTPUTS_JSON")"
K8S_VERSION="$(jq -r '.cluster_version.value' "$OUTPUTS_JSON")"

APP_RELEASE="${APP_RELEASE:-lab06-app}"
MON_RELEASE="${MON_RELEASE:-monitoring}"
MON_NS="${MON_NAMESPACE:-monitoring}"
KPS_VERSION="${KPS_VERSION:-91.8.2}"
EXERCISE_INSTALL="${EXERCISE_INSTALL:-1}"
EXERCISE_FAULT="${EXERCISE_FAULT:-1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART="$HERE/helm/lab06-app"
MON_VALUES="$HERE/helm/monitoring/values.yaml"

export KUBECONFIG="${KUBECONFIG:-$EVID/kubeconfig}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

echo "Exercising lab 07: cluster $CLUSTER ($REGION, Kubernetes $K8S_VERSION)"
rc=0
PF_PIDS=()
cleanup() { for p in "${PF_PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 0. kubeconfig: IAM identity -> EKS access entry -> Kubernetes RBAC.
# ---------------------------------------------------------------------------
echo "--- aws eks update-kubeconfig ---"
aws eks update-kubeconfig --region "$REGION" --name "$CLUSTER" --kubeconfig "$KUBECONFIG" >/dev/null
kubectl version -o json > "$EVID/kubectl-version.json" 2>/dev/null || true
echo "server: $(jq -r '.serverVersion.gitVersion // "unknown"' "$EVID/kubectl-version.json")"

# ---------------------------------------------------------------------------
# 1. Nodes Ready.
# ---------------------------------------------------------------------------
echo "--- Nodes ---"
READY_NODES=0
for i in $(seq 1 24); do
  kubectl get nodes -o json > "$EVID/nodes.json" 2>/dev/null || echo '{"items":[]}' > "$EVID/nodes.json"
  READY_NODES="$(jq '[.items[] | select(.status.conditions[] | select(.type=="Ready" and .status=="True"))] | length' "$EVID/nodes.json")"
  [ "$READY_NODES" -ge "$NODE_COUNT" ] && break
  echo "attempt $i/24: $READY_NODES/$NODE_COUNT nodes Ready"; sleep 10
done
kubectl get nodes -o wide | tee "$EVID/nodes.txt"

# ---------------------------------------------------------------------------
# 2. Helm installs (local path). CI does exactly these commands in its deploy job.
# ---------------------------------------------------------------------------
if [ "$EXERCISE_INSTALL" = "1" ]; then
  IMAGE_TAG="${IMAGE_TAG:-$(aws ecr describe-images --region "$REGION" --repository-name "$ECR_NAME" \
    --query 'sort_by(imageDetails,&imagePushedAt)[-1].imageTags[0]' --output text)}"
  echo "--- helm upgrade --install $MON_RELEASE (kube-prometheus-stack $KPS_VERSION) ---"
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
  helm repo update prometheus-community >/dev/null
  GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)}"
  helm upgrade --install "$MON_RELEASE" prometheus-community/kube-prometheus-stack --version "$KPS_VERSION" \
    -n "$MON_NS" --create-namespace -f "$MON_VALUES" \
    --set "grafana.adminPassword=${GRAFANA_ADMIN_PASSWORD}" --wait --timeout 12m
  echo "--- helm upgrade --install $APP_RELEASE (image tag $IMAGE_TAG) ---"
  helm upgrade --install "$APP_RELEASE" "$CHART" -n "$APP_NS" --create-namespace -f "$CHART/values-demo.yaml" \
    --set "image.repository=${ECR_URL}" --set "image.tag=${IMAGE_TAG}" \
    --set "serviceAccount.roleArn=${IRSA_ROLE}" --set "serviceAccount.name=${APP_SA}" \
    --set "monitoring.releaseLabel=${MON_RELEASE}" --wait --timeout 8m
fi
helm list -A -o json > "$EVID/helm-list.json" 2>/dev/null || echo '[]' > "$EVID/helm-list.json"
jq -r '.[] | "\(.namespace)/\(.name)  \(.chart)  \(.status)"' "$EVID/helm-list.json"

# ---------------------------------------------------------------------------
# 3. Rollout + ready pods.
# ---------------------------------------------------------------------------
echo "--- kubectl rollout status ---"
ROLLOUT_OK=0
if kubectl -n "$APP_NS" rollout status "deploy/$APP_RELEASE" --timeout=5m | tee "$EVID/rollout.txt"; then ROLLOUT_OK=1; fi
kubectl -n "$APP_NS" get pods -o json > "$EVID/pods.json"
READY_PODS="$(jq '[.items[] | select(.status.phase=="Running") | select([.status.containerStatuses[]?.ready] | all)] | length' "$EVID/pods.json")"
kubectl -n "$APP_NS" get pods -o wide | tee "$EVID/pods.txt"
kubectl -n "$APP_NS" get deploy,svc,hpa,pdb,sa -o wide > "$EVID/app-objects.txt" 2>&1 || true
IMAGE_RUNNING="$(jq -r '[.items[].spec.containers[0].image] | unique | join(",")' "$EVID/pods.json")"
echo "running image(s): $IMAGE_RUNNING"

# ---------------------------------------------------------------------------
# 4. The NLB: hostname appears in ~2 min, DNS resolves ~1-3 min later.
# ---------------------------------------------------------------------------
echo "--- NLB hostname ---"
NLB=""
for i in $(seq 1 36); do
  NLB="$(kubectl -n "$APP_NS" get svc "$APP_RELEASE" -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
  [ -n "$NLB" ] && break
  echo "attempt $i/36: waiting for the load balancer"; sleep 5
done
kubectl -n "$APP_NS" get svc "$APP_RELEASE" -o json > "$EVID/service.json" 2>/dev/null || true
echo "nlb: ${NLB:-<none>}" | tee "$EVID/nlb-hostname.txt"

HEALTH_CODE="000"
if [ -n "$NLB" ]; then
  echo "--- Polling http://${NLB}/healthz (up to ~6 min: NLB provisioning + DNS) ---"
  for i in $(seq 1 72); do
    HEALTH_CODE="$(curl -s -o "$EVID/healthz.json" -D "$EVID/healthz-headers.txt" -w '%{http_code}' --max-time 5 "http://${NLB}/healthz" || echo 000)"
    echo "attempt $i/72: HTTP $HEALTH_CODE"
    [ "$HEALTH_CODE" = "200" ] && break
    sleep 5
  done
  curl -s --max-time 5 "http://${NLB}/version" -o "$EVID/version.json" || true
  curl -s --max-time 5 "http://${NLB}/metrics" -o "$EVID/metrics.txt" || true
  echo "version: $(cat "$EVID/version.json" 2>/dev/null || echo '<none>')"
fi
echo "healthz HTTP code: $HEALTH_CODE" | tee "$EVID/healthz-code.txt"

# ---------------------------------------------------------------------------
# 5. IRSA: the pod-identity webhook must have injected the role + token.
#    The image has no AWS CLI, so we read what the webhook put in the pod.
# ---------------------------------------------------------------------------
echo "--- IRSA injection ---"
POD="$(jq -r '[.items[] | select(.status.phase=="Running")][0].metadata.name // empty' "$EVID/pods.json")"
IRSA_ENV_ROLE=""; IRSA_TOKEN=""
if [ -n "$POD" ]; then
  kubectl -n "$APP_NS" exec "$POD" -- env 2>/dev/null | grep -E '^AWS_(ROLE_ARN|WEB_IDENTITY_TOKEN_FILE|REGION|DEFAULT_REGION|STS_REGIONAL_ENDPOINTS)=' > "$EVID/irsa-env.txt" || true
  IRSA_ENV_ROLE="$(sed -n 's/^AWS_ROLE_ARN=//p' "$EVID/irsa-env.txt")"
  IRSA_TOKEN="$(kubectl -n "$APP_NS" exec "$POD" -- python3 -c 'import os,sys; p=os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE",""); sys.stdout.write("present" if p and os.path.exists(p) and os.path.getsize(p)>0 else "missing")' 2>/dev/null || echo "missing")"
  cat "$EVID/irsa-env.txt"; echo "token file: $IRSA_TOKEN"
fi

# ---------------------------------------------------------------------------
# 6. HPA has a CPU reading (proves metrics-server -> metrics.k8s.io -> HPA).
# ---------------------------------------------------------------------------
echo "--- HPA ---"
HPA_CPU=""
for i in $(seq 1 18); do
  kubectl -n "$APP_NS" get hpa "$APP_RELEASE" -o json > "$EVID/hpa.json" 2>/dev/null || echo '{}' > "$EVID/hpa.json"
  HPA_CPU="$(jq -r '[.status.currentMetrics[]? | select(.type=="Resource" and .resource.name=="cpu") | .resource.current.averageUtilization] | first // empty' "$EVID/hpa.json")"
  [ -n "$HPA_CPU" ] && break
  echo "attempt $i/18: HPA has no CPU reading yet"; sleep 10
done
kubectl -n "$APP_NS" get hpa "$APP_RELEASE" 2>/dev/null | tee "$EVID/hpa.txt" || true
kubectl top pods -A --no-headers > "$EVID/top-pods.txt" 2>/dev/null || true
kubectl top nodes > "$EVID/top-nodes.txt" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 7. Prometheus: our ServiceMonitor target is UP and our rules are loaded.
# ---------------------------------------------------------------------------
echo "--- Prometheus (port-forward) ---"
kubectl -n "$MON_NS" port-forward "svc/${MON_RELEASE}-prometheus" 19090:9090 >/dev/null 2>&1 &
PF_PIDS+=($!)
PROM="http://127.0.0.1:19090"
TARGET_UP=0
for i in $(seq 1 24); do
  if curl -s --max-time 5 "$PROM/api/v1/targets?state=active" -o "$EVID/prom-targets.json"; then
    TARGET_UP="$(jq --arg ns "$APP_NS" --arg svc "$APP_RELEASE" '[.data.activeTargets[] | select(.labels.namespace==$ns and .labels.service==$svc and .health=="up")] | length' "$EVID/prom-targets.json" 2>/dev/null || echo 0)"
    [ "$TARGET_UP" -ge 1 ] && break
  fi
  echo "attempt $i/24: $TARGET_UP app targets up"; sleep 5
done
echo "app targets up: $TARGET_UP"
curl -s --max-time 10 "$PROM/api/v1/rules?type=alert" -o "$EVID/prom-rules.json" || echo '{}' > "$EVID/prom-rules.json"
RULES_FOUND="$(jq '[.data.groups[]?.rules[]? | select(.name=="Lab06AppPodRestarting" or .name=="Lab06AppHigh5xxRate")] | length' "$EVID/prom-rules.json" 2>/dev/null || echo 0)"
echo "lab06-app alert rules loaded: $RULES_FOUND/2"

# ---------------------------------------------------------------------------
# 8. Grafana: login page up, API healthy, our dashboard provisioned.
# ---------------------------------------------------------------------------
echo "--- Grafana (port-forward) ---"
kubectl -n "$MON_NS" port-forward "svc/${MON_RELEASE}-grafana" 13000:80 >/dev/null 2>&1 &
PF_PIDS+=($!)
GRAFANA="http://127.0.0.1:13000"
GRAFANA_CODE="000"
for i in $(seq 1 12); do
  GRAFANA_CODE="$(curl -s -o "$EVID/grafana-login.html" -w '%{http_code}' --max-time 5 "$GRAFANA/login" || echo 000)"
  [ "$GRAFANA_CODE" = "200" ] && break
  echo "attempt $i/12: grafana /login HTTP $GRAFANA_CODE"; sleep 5
done
echo "grafana /login HTTP code: $GRAFANA_CODE" | tee "$EVID/grafana-login-code.txt"
curl -s --max-time 5 "$GRAFANA/api/health" -o "$EVID/grafana-health.json" || echo '{}' > "$EVID/grafana-health.json"
GRAFANA_PW="$(kubectl -n "$MON_NS" get secret "${MON_RELEASE}-grafana" -o jsonpath='{.data.admin-password}' 2>/dev/null | base64 -d || true)"
DASH_FOUND=0
if [ -n "$GRAFANA_PW" ]; then
  for i in $(seq 1 18); do
    curl -s --max-time 5 -u "admin:${GRAFANA_PW}" "$GRAFANA/api/search?query=Lab%2007" -o "$EVID/grafana-dashboard-search.json" || echo '[]' > "$EVID/grafana-dashboard-search.json"
    DASH_FOUND="$(jq '[.[] | select(.uid=="awslabs-lab07-app")] | length' "$EVID/grafana-dashboard-search.json" 2>/dev/null || echo 0)"
    [ "$DASH_FOUND" -ge 1 ] && break
    echo "attempt $i/18: dashboard not provisioned yet"; sleep 10
  done
fi
echo "lab07 dashboard provisioned: $DASH_FOUND"

# ---------------------------------------------------------------------------
# 9. Fault injection: make the 5xx alert go pending/firing, then stop.
#    (A detector that has never fired is a detector that has never been tested.)
# ---------------------------------------------------------------------------
ALERT_STATE="skipped"
if [ "$EXERCISE_FAULT" = "1" ] && [ -n "$NLB" ] && [ "$HEALTH_CODE" = "200" ]; then
  echo "--- Fault injection: 120 × GET /boom, then watch Lab06AppHigh5xxRate ---"
  for _ in $(seq 1 120); do curl -s -o /dev/null --max-time 3 "http://${NLB}/boom" || true; done
  ALERT_STATE="inactive"
  for i in $(seq 1 30); do
    curl -s --max-time 5 "$PROM/api/v1/alerts" -o "$EVID/prom-alerts.json" || echo '{}' > "$EVID/prom-alerts.json"
    ALERT_STATE="$(jq -r '[.data.alerts[]? | select(.labels.alertname=="Lab06AppHigh5xxRate")] | if length==0 then "inactive" else (map(.state) | if any(.=="firing") then "firing" else .[0] end) end' "$EVID/prom-alerts.json" 2>/dev/null || echo inactive)"
    echo "attempt $i/30: Lab06AppHigh5xxRate = $ALERT_STATE"
    [ "$ALERT_STATE" = "firing" ] && break
    sleep 10
  done
fi
echo "5xx alert state after fault injection: $ALERT_STATE" | tee "$EVID/alert-state.txt"

# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------
echo "--- Assertions ---"
if [ "$READY_NODES" -ge "$NODE_COUNT" ]; then echo "PASS: $READY_NODES nodes Ready (expected $NODE_COUNT)"; else echo "FAIL: only $READY_NODES/$NODE_COUNT nodes Ready"; rc=1; fi
if [ "$ROLLOUT_OK" = "1" ]; then echo "PASS: deployment $APP_NS/$APP_RELEASE rolled out"; else echo "FAIL: rollout status did not complete"; rc=1; fi
if [ "$READY_PODS" -ge 2 ]; then echo "PASS: $READY_PODS ready app pods (>= 2)"; else echo "FAIL: only $READY_PODS ready app pods"; rc=1; fi
if [ "$HEALTH_CODE" = "200" ] && [ "$(jq -r '.status' "$EVID/healthz.json" 2>/dev/null)" = "ok" ]; then echo "PASS: NLB /healthz returned 200 {\"status\":\"ok\"} via $NLB"; else echo "FAIL: NLB /healthz never returned 200 (last: $HEALTH_CODE)"; rc=1; fi
if [ -n "$IRSA_ENV_ROLE" ] && [ "$IRSA_ENV_ROLE" = "$IRSA_ROLE" ] && [ "$IRSA_TOKEN" = "present" ]; then echo "PASS: IRSA injected AWS_ROLE_ARN=$IRSA_ENV_ROLE + token file into the pod"; else echo "FAIL: IRSA not injected (role='$IRSA_ENV_ROLE' token=$IRSA_TOKEN, expected $IRSA_ROLE)"; rc=1; fi
if [ -n "$HPA_CPU" ]; then echo "PASS: HPA reports CPU utilization ${HPA_CPU}% (metrics-server is serving)"; else echo "FAIL: HPA never got a CPU reading (metrics-server?)"; rc=1; fi
if [ "$TARGET_UP" -ge 1 ]; then echo "PASS: Prometheus scrapes $TARGET_UP lab06-app target(s), health=up"; else echo "FAIL: no lab06-app target up in Prometheus"; rc=1; fi
if [ "$RULES_FOUND" = "2" ]; then echo "PASS: both lab06-app alert rules loaded in Prometheus"; else echo "FAIL: $RULES_FOUND/2 lab06-app alert rules loaded"; rc=1; fi
if [ "$GRAFANA_CODE" = "200" ]; then echo "PASS: Grafana login page returned 200"; else echo "FAIL: Grafana login page returned $GRAFANA_CODE"; rc=1; fi
if [ "$DASH_FOUND" -ge 1 ]; then echo "PASS: Grafana provisioned the lab 07 dashboard (uid awslabs-lab07-app)"; else echo "WARN: lab 07 dashboard not found in Grafana (sidecar lag or search auth)"; fi
case "$ALERT_STATE" in
  firing|pending) echo "PASS: Lab06AppHigh5xxRate went $ALERT_STATE after fault injection (the detector can fail)";;
  skipped)        echo "WARN: fault injection skipped";;
  *)              echo "WARN: Lab06AppHigh5xxRate stayed $ALERT_STATE within 5 min of fault injection (scrape/eval timing)";;
esac
if [ -n "$NLB" ]; then
  echo "note: Grafana is NOT exposed; locally: kubectl -n $MON_NS port-forward svc/${MON_RELEASE}-grafana 3000:80  (admin / kubectl -n $MON_NS get secret ${MON_RELEASE}-grafana -o jsonpath='{.data.admin-password}' | base64 -d)"
fi

exit "$rc"

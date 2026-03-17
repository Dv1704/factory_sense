#!/bin/bash

# Configuration
BASE_URL=${1:-"http://144.91.111.151:8000/api/v1"}
echo "Using BASE_URL: $BASE_URL"
EMAIL="test_$(date +%s)@example.com"
PASSWORD="password123"
MILL_NAME="TEST_MILL_$(date +%s)"
MILL_TAG="TAG_$(date +%s)"

echo "=== FactorySenseAI API Comprehensive Test ==="

# 1. Auth: Register
echo "1. Testing Auth: Register..."
REGISTER_RESP=$(curl -s -X POST "$BASE_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$EMAIL\", \"password\": \"$PASSWORD\", \"mill_name\": \"$MILL_NAME\", \"mill_tag\": \"$MILL_TAG\"}")

echo "Response: $REGISTER_RESP"
ACCESS_TOKEN=$(echo $REGISTER_RESP | grep -o '"access_token":"[^"]*' | cut -d'"' -f4)
API_KEY=$(echo $REGISTER_RESP | grep -o '"api_key":"[^"]*' | cut -d'"' -f4)

if [ -z "$ACCESS_TOKEN" ] || [ -z "$API_KEY" ]; then
    echo "FAILED to register or extract tokens. Exiting."
    exit 1
fi
echo "SUCCESS: Registered User with API Key $API_KEY"

# 1.5 Verify Email
echo -e "\n1.5. Testing Auth: Verify Email..."
VERIFY_RESP=$(curl -s -X POST "$BASE_URL/auth/verify-email" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$EMAIL\", \"token\": \"VERIFY_123\"}")
echo "Response: $VERIFY_RESP"

# 2. Auth: Me
echo -e "\n2. Testing Auth: Me..."
curl -s -X GET "$BASE_URL/auth/me" -H "Authorization: Bearer $ACCESS_TOKEN"

# 3. Auth: Login
echo -e "\n3. Testing Auth: Login..."
curl -s -X POST "$BASE_URL/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=$EMAIL&password=$PASSWORD"

# 4. Dashboard: Machine Specs
echo -e "\n4. Testing Dashboard: Machine Specs..."
curl -s -X GET "$BASE_URL/dashboard/machine-specs" -H "x-api-key: $API_KEY"

# 5. Data: Baseline Upload
echo -e "\n5. Testing Data: Baseline Upload..."
echo "timestamp,mill_id,machine_id,current_A,motor_state" > test_baseline.csv
echo "$(date -u +"%Y-%m-%d %H:%M:%S"),$MILL_TAG,M1,10.0,RUNNING" >> test_baseline.csv
echo "$(date -u +"%Y-%m-%d %H:%M:%S"),$MILL_TAG,M2,15.0,RUNNING" >> test_baseline.csv

BASELINE_RESP=$(curl -s -X POST "$BASE_URL/baseline/upload" \
  -H "x-api-key: $API_KEY" \
  -F "file=@test_baseline.csv")
echo "Response: $BASELINE_RESP"
TASK_ID=$(echo $BASELINE_RESP | grep -o '"task_id":"[^"]*' | cut -d'"' -f4)

# 6. Data: Task Status (Polling)
echo -e "\n6. Polling Task Status for $TASK_ID..."
while true; do
  STATUS_RESP=$(curl -s -X GET "$BASE_URL/task/$TASK_ID" -H "x-api-key: $API_KEY")
  STATUS=$(echo $STATUS_RESP | grep -o '"status":"[^"]*' | cut -d'"' -f4)
  echo "Current Status: $STATUS"
  if [ "$STATUS" == "COMPLETED" ]; then
    break
  fi
  if [ "$STATUS" == "FAILED" ]; then
    echo "Task Failed! Response: $STATUS_RESP"
    exit 1
  fi
  sleep 1
done

# 7. Data: List Baselines
echo -e "\n7. Testing Data: List Baselines..."
curl -s -X GET "$BASE_URL/baseline" -H "x-api-key: $API_KEY"

# 8. Data: Operational Data Upload
echo -e "\n8. Testing Data: Operational Data Upload..."
echo "timestamp,mill_id,machine_id,current_A,motor_state" > test_op.csv
# Add some data points (including many to check batching if desired, but 10 is enough for simple check)
for i in {1..10}; do
  echo "$(date -u -d "$i minutes ago" +"%Y-%m-%d %H:%M:%S"),$MILL_TAG,M1,12.0,RUNNING" >> test_op.csv
done

OP_RESP=$(curl -s -X POST "$BASE_URL/upload" \
  -H "x-api-key: $API_KEY" \
  -F "file=@test_op.csv")
echo "Response: $OP_RESP"
OP_TASK_ID=$(echo $OP_RESP | grep -o '"task_id":"[^"]*' | cut -d'"' -f4)

# Wait for OP Task
echo "Waiting for Operational Task $OP_TASK_ID..."
while true; do
  STATUS_RESP=$(curl -s -X GET "$BASE_URL/task/$OP_TASK_ID" -H "x-api-key: $API_KEY")
  STATUS=$(echo $STATUS_RESP | grep -o '"status":"[^"]*' | cut -d'"' -f4)
  if [ "$STATUS" == "COMPLETED" ]; then break; fi
  if [ "$STATUS" == "FAILED" ]; then exit 1; fi
  sleep 1
done

# 9. Dashboard: Summary
echo -e "\n9. Testing Dashboard: Summary..."
curl -s -X GET "$BASE_URL/dashboard/summary" -H "x-api-key: $API_KEY"

# 10. Dashboard: Machines
echo -e "\n10. Testing Dashboard: Machines..."
curl -s -X GET "$BASE_URL/dashboard/machines" -H "x-api-key: $API_KEY"

# 11. Alerts: List
echo -e "\n11. Testing Alerts: List..."
ALERTS_RESP=$(curl -s -X GET "$BASE_URL/alerts/" -H "x-api-key: $API_KEY")
echo "Response: $ALERTS_RESP"
ALERT_ID=$(echo $ALERTS_RESP | grep -o '"id":[0-9]*' | head -1 | cut -d':' -f2)

# 12. Alerts: Acknowledge (if alert exists)
if [ ! -z "$ALERT_ID" ]; then
  echo -e "\n12. Testing Alerts: Acknowledge $ALERT_ID..."
  curl -s -X POST "$BASE_URL/alerts/$ALERT_ID/acknowledge" -H "x-api-key: $API_KEY"
else
  echo -e "\n12. No alerts found to acknowledge (this is normal if data is within baseline)."
fi

# Cleanup
rm test_baseline.csv test_op.csv

echo -e "\n=== API Test Summary ==="
echo "Basic flow completed. For Admin APIs, the user needs to be manually promoted to ADMIN role in the database."

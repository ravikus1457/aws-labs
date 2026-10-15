# Lab 09 load test (2026-10-15 21:33 UTC)

| Measure | Value | Source |
|---|---|---|
| Requests sent | 2,000 at concurrency 8 in 54.5 s (37 req/s) | client |
| Success rate (HTTP 202) | 100.00% - codes {'202': 2000} | client |
| Client latency p50 / p99 | 201 ms / 386 ms | client wall clock |
| API Gateway latency p50 / p99 (per-minute max) | 35 ms / 223 ms | CloudWatch |
| Accepted -> persisted | 2,000 / 2,000 (0 missing, 0 needed a retry) | DynamoDB read-back |
| End-to-end p50 / p99 (accepted -> persisted) | 57,120 ms / 108,269 ms | item timestamps |
| Queue drained after sending | 111 s (polled every 15 s) | DynamoDB read-back |
| Max concurrency: client in-flight / ingest environments / CloudWatch ingest max | 9 / 8 / 8.0 | client / responses / CloudWatch |
| Account Lambda concurrency limit | 10 | lambda:GetAccountSettings |
| Lambda throttles (all functions) | 0 | CloudWatch |
| Step Functions executions started / failed | 2001 / 0 | CloudWatch |

## Cost of this run

Usage below is from CloudWatch for the run window.

| Item | Quantity | Arithmetic | Cost (USD) |
|---|---|---|---|
| API Gateway REST requests | 2000 requests | 2000 x $3.5/1M | $0.007000 |
| Lambda requests (all 4 functions) | 6205 invocations | 6205 x $0.2/1M | $0.001241 |
| Lambda duration (arm64) | 41.7845 GB-seconds | (enrich 62786 ms (billed) x 128 MB; ingest 87501 ms (billed) x 128 MB; validate 5447 ms (billed) x 128 MB; worker 178542 ms (billed) x 128 MB) = 41.7845 GB-s x $1.33334e-05 | $0.000557 |
| Step Functions Express requests | 2001 executions | 2001 x $1.0/1M | $0.002001 |
| Step Functions Express duration (estimate) | 25.0125 GB-seconds | 2001 x ceil(146.1 ms -> 100 ms) x 64 MB = 25.0125 GB-s x $1.667e-05 | $0.000417 |
| DynamoDB write request units | 2003 WRU | 2003 x $0.625/1M | $0.001252 |
| DynamoDB read request units | 5744.5 RRU | 5744.5 x $0.125/1M | $0.000718 |
| SQS requests (upper bound) | 6066 requests | 6066 x $0.4/1M | $0.002426 |
| X-Ray traces recorded | 949 traces | 949 x $5.0/1M | $0.004745 |
| X-Ray traces retrieved/scanned | 949 traces | 949 x $0.5/1M | $0.000474 |
| CloudWatch Logs ingestion | 6.30129e+06 bytes | 6301290 B = 0.005869 GB x $0.5 | $0.002934 |
| CloudWatch alarms (prorated) | 5 alarms | 5 x $0.1/month x 1 h / 730 h | $0.000685 |
| CloudWatch dashboard (prorated) | 1 dashboards | 1 x $3.0/month x 1 h / 730 h | $0.004110 |
| CloudWatch GetMetricData (this report) | 39 metrics | 39 x $0.01/1000 | $0.000390 |
| CloudWatch Logs Insights (this report) | 6.32201e+06 bytes scanned | 6.32201e+06 B / 1024^3 x $0.005 | $0.000029 |
| **Total (list price, before free tier)** | | | **$0.0290** |

Per 1,000 persisted orders: $0.0145.

Prices: 2026-10-15 (us-east-1 list prices as recorded here; verify on the linked pages).

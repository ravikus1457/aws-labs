# One on-demand table, two kinds of item (single-table design):
#   pk = ORDER#<order_id>  written ONLY by the state machine's Persist step
#   pk = SKU#<sku>         the catalog, written here (and by the smoke test's "fix")
# IAM uses the key prefix: the enrich Lambda may read SKU#* only, the state machine
# may write ORDER#* only (dynamodb:LeadingKeys conditions in lambda.tf / stepfunctions.tf).

resource "aws_dynamodb_table" "orders" {
  name                        = "${local.name}-orders"
  billing_mode                = "PAY_PER_REQUEST" # no capacity to size, $0 when idle
  hash_key                    = "pk"
  deletion_protection_enabled = false # ephemeral lab: destroyed nightly

  attribute {
    name = "pk"
    type = "S"
  }

  # Orders carry expires_at (accepted_at + 7 days); DynamoDB deletes them for free.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  # PITR off: continuous backups bill per GB-month and the data is synthetic.
  point_in_time_recovery {
    enabled = false
  }
}

resource "aws_dynamodb_table_item" "catalog" {
  for_each   = var.catalog
  table_name = aws_dynamodb_table.orders.name
  hash_key   = aws_dynamodb_table.orders.hash_key

  item = jsonencode({
    pk          = { S = "SKU#${each.key}" }
    sku         = { S = each.key }
    name        = { S = each.value.name }
    price_cents = { N = tostring(each.value.price_cents) }
  })
}

################################################################################
# Requests: one item per request for its whole life (submission, decision, and
# every provisioning step). The stream starts the workflow on approval.
################################################################################

resource "aws_dynamodb_table" "requests" {
  name         = "${var.name_prefix}-requests"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "RequestId"

  attribute {
    name = "RequestId"
    type = "S"
  }

  attribute {
    name = "AccountId"
    type = "S"
  }

  attribute {
    name = "CreatedAt"
    type = "N"
  }

  global_secondary_index {
    name = "AccountId-CreatedAt-index"

    key_schema {
      attribute_name = "AccountId"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "CreatedAt"
      key_type       = "RANGE"
    }

    projection_type = "ALL"
  }

  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"

  ttl {
    attribute_name = "ExpiresAt"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = var.deletion_protection
}

################################################################################
# Accounts: who may approve requests for each AWS account
################################################################################

resource "aws_dynamodb_table" "accounts" {
  name         = "${var.name_prefix}-accounts"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "AccountId"

  attribute {
    name = "AccountId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = var.deletion_protection
}

# Item attributes are DynamoDB string sets. A set cannot be empty, so deputies
# are only written when there are some. Names are stored in lower case.
resource "aws_dynamodb_table_item" "account" {
  for_each = var.accounts

  table_name = aws_dynamodb_table.accounts.name
  hash_key   = "AccountId"

  item = jsonencode(merge(
    {
      AccountId = { S = each.key }
      Owners    = { SS = distinct([for name in each.value.owners : lower(name)]) }
    },
    length(each.value.deputies) > 0 ? { Deputies = { SS = distinct([for name in each.value.deputies : lower(name)]) } } : {}
  ))
}

################################################################################
# Users: maps the Entra ID user name to the IAM Identity Center user
#
# Key: EntraUsername, the name the authorizer passes (lower case, after
# strip_username_domains). Attributes: UserId (Identity Center user ID, used for
# the assignment) and IdentityCenterUserName (for people reading the table).
# A user without an item here cannot request access.
################################################################################

resource "aws_dynamodb_table" "users" {
  name         = "${var.name_prefix}-users"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "EntraUsername"

  attribute {
    name = "EntraUsername"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = var.deletion_protection
}

# tflint config for lab 09 (used by .github/workflows/lab09.yml).
# Only the bundled "terraform" ruleset: no plugin download, runs in seconds.
plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

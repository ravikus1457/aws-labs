# tflint config for lab 06 (used by .github/workflows/lab06.yml).
# Only the bundled "terraform" ruleset: no plugin download, runs in seconds.
plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

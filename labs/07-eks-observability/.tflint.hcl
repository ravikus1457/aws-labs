# tflint config for lab 07 (used by .github/workflows/lab07.yml).
# Only the bundled "terraform" ruleset: no plugin download, runs in seconds.
plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

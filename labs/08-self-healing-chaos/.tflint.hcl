# tflint config for lab 08 (used by .github/workflows/lab08.yml).
# Only the bundled "terraform" ruleset: no plugin download, runs in seconds.
plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

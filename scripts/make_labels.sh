#!/usr/bin/env bash
# scripts/make_labels.sh
# Idempotently create or update GitHub labels and milestones for mswap.
set -euo pipefail

REPO="shaurya-disciplined/mswap"

echo "Creating or updating GitHub labels for $REPO..."

create_label() {
  local name="$1"
  local color="$2"
  local description="$3"
  echo "  label: $name"
  gh label create "$name" --repo "$REPO" --color "$color" --description "$description" --force
}

# Waves (§H5)
create_label "wave:0" "0075ca" "Wave 0: Foundation"
create_label "wave:1" "1d76db" "Wave 1: Core engine"
create_label "wave:2" "0e8a16" "Wave 2: Live agy + launch"
create_label "wave:3" "006b75" "Wave 3: Usage intelligence"
create_label "wave:4" "5319e7" "Wave 4: Autopilot"
create_label "wave:5" "d93f0b" "Wave 5: Cross-platform"
create_label "wave:6" "fbca04" "Wave 6: Portability & polish"
create_label "wave:7" "b60205" "Wave 7: v1.0"

# Types (§H5)
create_label "type:feat" "a2eeef" "New feature or capability"
create_label "type:fix" "d73a4a" "Bug fix"
create_label "type:docs" "0075ca" "Documentation changes"
create_label "type:chore" "cfd3d7" "Maintenance or routine task"
create_label "type:spike" "d876e3" "Research spike or investigation"

# Areas (§H5)
create_label "area:vault" "c5def5" "Credential storage and vault backends"
create_label "area:agy" "bfdadc" "Antigravity CLI integration and paths"
create_label "area:switch" "c2e0c6" "Account switching logic"
create_label "area:ui" "f9d0c4" "Terminal UI and rendering"
create_label "area:autopilot" "e99695" "Autopilot engine and scheduling"
create_label "area:platform" "d4c5f9" "Platform-specific code and adapters"
create_label "area:release" "fef2c0" "Packaging, shipping, and release"

# Special (§H5)
create_label "risk:secrets" "b60205" "Touches credentials, tokens, or redaction"
create_label "human-gate" "e11d48" "Requires human action or decision"
create_label "good first issue" "7057ff" "Good for newcomers"

echo "Creating milestones for $REPO..."

existing_milestones=$(gh api "repos/$REPO/milestones?state=all" --jq '.[].title' 2>/dev/null || true)

create_milestone_if_missing() {
  local title="$1"
  if echo "$existing_milestones" | grep -Fxq "$title"; then
    echo "  milestone already exists: $title"
  else
    echo "  creating milestone: $title"
    gh api "repos/$REPO/milestones" -f title="$title" >/dev/null
  fi
}

create_milestone_if_missing "W0 Foundation (v0.1.0)"
create_milestone_if_missing "W1 Core engine (v0.2.0)"
create_milestone_if_missing "W2 Live agy + launch (v0.3.0)"
create_milestone_if_missing "W3 Usage intelligence (v0.4.0)"
create_milestone_if_missing "W4 Autopilot (v0.5.0)"
create_milestone_if_missing "W5 Cross-platform (v0.6.0)"
create_milestone_if_missing "W6 Portability & polish (v0.9.0rc1)"
create_milestone_if_missing "W7 v1.0 (v1.0.0)"

echo "Labels and milestones configuration complete."

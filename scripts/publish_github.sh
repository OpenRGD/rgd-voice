#!/usr/bin/env bash
# Create a brand-new private OpenRGD/rgd-voice repository from this audited kit.
# Does not touch rgd-console, rgd-modules or runtime volumes.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
REPO_DIR="$(pwd -P)"
echo '=== OpenRGD Voice 0.3.1 — bootstrap repository ==='
[ "${REPO_DIR}" = "${HOME}/openrgd/repos/rgd-voice" ] || {
  echo "ERRORE: estrai il kit in ~/openrgd/repos/rgd-voice (attuale: $REPO_DIR)" >&2
  exit 1
}
for c in git gh python3; do command -v "$c" >/dev/null || { echo "Comando mancante: $c"; exit 1; }; done
if [ -e .git ]; then echo 'ERRORE: repository Git già inizializzata: non sovrascrivo.'; exit 1; fi
if gh repo view OpenRGD/rgd-voice --json name --jq '.name' >/dev/null 2>&1; then
  echo 'ERRORE: OpenRGD/rgd-voice esiste già. Non sovrascrivo.' >&2
  exit 1
fi
# Credential error must not be mistaken for a missing repository.
gh auth status >/dev/null || { echo 'Autenticazione GitHub CLI non valida.'; exit 1; }
python3 scripts/check_baseline.py
PY="${HOME}/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python"
[ -x "$PY" ] || PY=python3
printf '== Running tests with %s ==\n' "$PY"
"$PY" -m unittest discover -s tests -v
python3 -m compileall -q rgd_voice_lab
python3 -m json.tool module.candidate.json >/dev/null
python3 -m json.tool contracts/voice-lifecycle.v0.1.schema.json >/dev/null

# We explicitly permit the initial publication to the new repository's main.
# Subsequent feature changes must go via PR. Console main is never modified.
git init -b main
git add -A
git diff --cached --check
python3 scripts/audit_staged.py
git diff --cached --stat
if ! git commit -m 'bootstrap: OpenRGD Voice Lab 0.3.1 candidate with Console handoff'; then
    echo 'Commit non riuscito: controlla git user.name e user.email.' >&2
    exit 1
fi
gh repo create OpenRGD/rgd-voice --private --source=. --remote=origin --push
printf '\n=== REMOTE ===\n'
git remote -v
printf '\n=== HEAD ===\n'
git log -1 --oneline
printf '\n=== STATUS ===\n'
git status --short --branch
printf '\nRepository: https://github.com/OpenRGD/rgd-voice\n'
printf 'Stato: sorgenti pubblicati, NON installabile via Console (richiede installer Python dedicato).\n'

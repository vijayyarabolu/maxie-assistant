PY ?= .venv/bin/python
PORT ?= 8780

.PHONY: install run demo test lint eval eval-live eval-useful audit-names check icons mascot laya services \
        notch notch-install notch-test notch-run

install:            ## venv + deps (uv)
	uv venv -q .venv && uv pip install -q -p .venv -e ".[dev]"

run:                ## the hub on http://127.0.0.1:8780; open it and set a passcode
	$(PY) -m maxie.hub.api

demo:               ## the demo hub: fake Google, jobs and Mac, a scripted brain, its own data folder, port 8781
	MAXIE_FAKES=1 HUB_TOKEN=devtoken HUB_PORT=8781 MAXIE_DATA=$${DEMO_DATA:-$$HOME/.maxie-demo} \
	  $(PY) -m maxie.hub.api

fake-hub: demo      ## the old name for `make demo`

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .

eval:               ## offline router eval (what CI runs); ROUTER=laya or haiku to score those steps
	$(PY) -m maxie.evals --router $${ROUTER:-offline}

eval-live:          ## 20 live-fact questions through the real agent: each needs a tool or an honest "can't check" (~$0.15)
	$(PY) -m maxie.evals_live --check

eval-useful:        ## 40 daily tasks through the real agent, pass bar 90% (~$0.12)
	$(PY) -m maxie.evals_useful --check

audit-names:        ## everything you see or hear says Maxie; also part of `make test`
	$(PY) scripts/audit_names.py

check: lint test    ## everything CI runs
	$(PY) -m maxie.evals --check

icons:
	$(PY) scripts/make_icons.py

mascot:             ## render the notch app's animation frames into assets/ (needs playwright + Chromium)
	$(PY) scripts/render_mascot.py
	$(PY) scripts/render_mascot.py --character classic

laya:               ## the optional local Laya router on 127.0.0.1:8000 (uv pip install -p .venv -e ".[laya]")
	$(PY) deploy/laya_serve.py

services:           ## macOS only: launchd agents for the hub, Laya, voice, nightly backup, log rotation
	deploy/install.sh all

# ---- the optional macOS notch app (mac/ClaudeNotch, needs Xcode and `brew install xcodegen`)
NOTCH = mac/ClaudeNotch
# Your installed Maxie.app: only `make notch-install` replaces it. `make notch` builds and verifies in a staging folder.
NOTCH_APP = $(NOTCH)/build/Build/Products/Debug/Maxie.app
NOTCH_STAGED = $(NOTCH)/build-stage.noindex/Build/Products/Debug/Maxie.app

notch:              ## build Maxie.app with the stable local signing identity into a staging folder, and verify it
	@test -d assets/mascot || { echo "No animation frames yet: run make mascot first."; exit 1; }
	scripts/notch_build.sh build-stage.noindex

notch-install: notch  ## swap the verified build in for the installed Maxie.app
	scripts/notch_install.sh $(NOTCH_STAGED) $(NOTCH_APP)

notch-test:         ## unit tests on their own copy (MaxieTestHost, dev.maxie.notch.tests), never the real app or its grants
	cd $(NOTCH) && xcodegen >/dev/null && xcodebuild -project ClaudeNotch.xcodeproj -scheme ClaudeNotch \
	  -configuration Debug -derivedDataPath build-tests MAXIE_BUNDLE_ID=dev.maxie.notch.tests \
	  MAXIE_NAME=MaxieTestHost CODE_SIGN_IDENTITY="-" -destination 'platform=macOS' test 2>&1 \
	  | grep -v "Connection\]" | grep -E "error:|failed|Executed|TEST"

notch-run: notch-install    ## (re)start the notch app
	-pkill -x ClaudeNotch
	-pkill -x Maxie
	open $(NOTCH_APP)

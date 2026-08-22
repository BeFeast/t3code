#!/usr/bin/env bash
# OK Labs: fan a CI alert out to Telegram and ntfy.
#
# Telegram goes FIRST, deliberately. The ntfy topic oklabs-forks has no
# subscriber; letting the dead channel run ahead of the live one means a
# wedged ntfy can burn the step's time budget before the only message a human
# will ever see gets sent. Every send is bounded and none can fail the caller:
# a notification outage must never be reported as a build failure.
#
# Usage: .oklabs/notify.sh <ntfy-priority> <message>
set -u

PRIORITY="${1:?priority required}"
MSG="${2:?message required}"

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  # The `|| true` sits OUTSIDE the substitution on purpose. curl emits its
  # -w write-out (000 on a transport failure) and only then exits non-zero, so
  # `$(curl ... || echo 000)` appends a second 000 and logs the non-status
  # 000000 — in exactly the branch this log line exists to report.
  code=$(curl -s -m 20 --connect-timeout 5 -o /tmp/oklabs-notify-tg.json \
    -w '%{http_code}' \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${MSG}") || true
  echo "telegram sendMessage -> ${code:-000}"
  if [ "${code:-000}" != "200" ]; then
    # Telegram error bodies carry the reason and never echo the token.
    echo "telegram error body: $(cat /tmp/oklabs-notify-tg.json 2>/dev/null)"
  fi
else
  echo "telegram secrets absent — skipped"
fi

curl -s -m 10 --connect-timeout 5 -H "Priority: ${PRIORITY}" -d "${MSG}" \
  https://ntfy.oklabs.uk/oklabs-forks >/dev/null 2>&1 || true
echo "ntfy -> best effort, no subscriber"

exit 0
